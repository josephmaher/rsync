#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Shared body of the --clone-dest reflink test suite. NOT a test itself (the
# filename doesn't end in "test.py", so runtests.py skips it). Every check is a
# function taking a `workdir` that must live on a reflink-capable filesystem;
# the thin clone-dest-<fs>_test.py wrappers make such a filesystem (or, for the
# agnostic clone-dest_test.py, probe the scratch one) and call
# run_all_clone_dest_checks() to run the whole battery on it.
#
# Each check roots all its files under a uniquely-named subdir of `workdir`, so
# the checks coexist in one workdir. `get_extents` is the extent reporter used
# to prove sharing (filefrag_extents everywhere, or xfs_bmap_extents on XFS).
# Daemon-using checks take a `port` (only used under --use-tcp; the default
# pipe transport ignores it).

import filecmp
import os
import re
import shutil
import subprocess

from rsyncfns import (
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, run_rsync, start_test_daemon, test_fail,
    make_tree, walk_files, assert_same, assert_exists,
    make_data_file, makepath, write_daemon_conf,
    xattr_set, xattr_dump, xattrs_supported,
    checkdiff, allspace, TOOLDIR,
)

FILEFRAG = '/sbin/filefrag'   # often not on a normal user's PATH


# ---- extent reporters: pass the right one to the checks --------------------

_FILEFRAG_RE = re.compile(r'^\s*\d+:')

def filefrag_extents(path):
    """Physical-offset start of every data extent, via filefrag (FIEMAP).

    Filesystem-agnostic. -s forces a sync so delalloc is flushed and offsets
    are stable; we pull the physical-offset start column from each numbered
    extent row (turning '..' into a field break first).
    """
    out = subprocess.run([FILEFRAG, '-s', '-v', str(path)],
                         capture_output=True, text=True).stdout
    return [line.replace('..', ' ').split()[3]
            for line in out.splitlines() if _FILEFRAG_RE.match(line)]


_XFS_RE = re.compile(r'^\d+:')

def xfs_bmap_extents(path):
    """Extent rows via XFS's native xfs_bmap, one normalized row per extent.

    Rows look like '0: [0..127]: 12345..12472'; the trailing pair is the
    physical block range, identical for reflinked files and different for
    independent copies. Unflushed data shows as 'delalloc'; the caller's sync
    plus the delalloc guard in clone_dest_reflink_check handle that.
    """
    out = subprocess.run(['xfs_bmap', str(path)], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        s = line.strip()
        if _XFS_RE.match(s):
            rows.append(' '.join(s.split()))
    return rows


def supports_reflink(directory):
    """True if `directory`'s filesystem can make a reflink.

    Probes with cp --reflink=always on a file above the inline-data threshold
    (inlined small files can't be cloned). cp computes the arch-correct FICLONE
    ioctl number for us, so this is more portable than a hardcoded constant.
    """
    src = directory / '.reflink_probe.src'
    dst = directory / '.reflink_probe.dst'
    try:
        src.write_bytes(os.urandom(64 * 1024))
        return subprocess.run(['cp', '--reflink=always', str(src), str(dst)],
                              capture_output=True).returncode == 0
    except OSError:
        return False
    finally:
        for p in (src, dst):
            try:
                p.unlink()
            except OSError:
                pass


# ---- individual checks -----------------------------------------------------

def clone_dest_reflink_check(workdir, get_extents):
    """--clone-dest must reflink an unchanged file, and only that file."""
    base = workdir / 'reflink'
    rmtree(base)
    base.mkdir(parents=True)
    d1, d2, d3 = base / '1', base / '2', base / '3'
    for d in (d1, d2, d3):
        d.mkdir()

    data = os.urandom(64 * 1024)
    (d1 / 'a').write_bytes(data)
    (d1 / 'b').write_bytes(data)
    (d3 / 'a').write_bytes(data)
    os.sync()  # bcachefs/xfs need data flushed before measuring extents

    clonedir = os.path.realpath(d3)
    proc = subprocess.run(
        rsync_argv('-a', f'--clone-dest={clonedir}', f'{d1}/', f'{d2}/'),
        capture_output=True, text=True)
    if proc.returncode != 0:
        test_fail(f"reflink: rsync --clone-dest failed (rc={proc.returncode})\n"
                  f"{proc.stdout}{proc.stderr}")
    os.sync()

    for name in ('a', 'b'):
        if not filecmp.cmp(d1 / name, d2 / name, shallow=False):
            test_fail(f"reflink: 2/{name} content differs from 1/{name}")

    ext_2a = get_extents(d2 / 'a')
    ext_3a = get_extents(d3 / 'a')
    ext_2b = get_extents(d2 / 'b')
    if any('delalloc' in t for t in ext_2a + ext_3a + ext_2b):
        test_fail("reflink: extent reporter shows delalloc (data not flushed)")
    if not ext_2a:
        test_fail("reflink: no extents found for 2/a (inlined, or unparsed?)")
    if ext_2a != ext_3a:
        test_fail("reflink: 2/a does not share extents with 3/a")
    # A reflink shares extents but is a DISTINCT inode -- separates clone-dest
    # from a hard link (which shares extents because it's the same inode).
    if os.stat(d2 / 'a').st_ino == os.stat(d3 / 'a').st_ino:
        test_fail("reflink: 2/a hard-linked to the basis instead of reflinked")
    if not ext_2b:
        test_fail("reflink: no extents found for 2/b")
    if ext_2b == ext_3a:
        test_fail("reflink: 2/b unexpectedly shares extents with the clone-dest")


def clone_dest_deep_check(workdir, get_extents):
    """--clone-dest at depth, with an outside-sibling reference: unchanged
    files reflink from the ref; a size-changed deep file transfers fresh."""
    base = workdir / 'deep'
    rmtree(base)
    base.mkdir(parents=True)
    src = base / 'from'
    ref = base / 'altref'
    to = base / 'to'

    # A >=3-deep source: f0, d1/f1, d1/d2/f2, d1/d2/d3/f3. data_size=64K clears any
    # inline-data threshold (inlined files can't be reflinked), matching the other
    # clone-dest tests.
    make_tree(src, depth=3, data=True, data_size=64 * 1024)

    # Reference == an exact copy of the source, so every file is a clone-dest match
    # (same size and mtime, which is what clone-dest's quick-check requires).
    run_rsync('-a', f'{src}/', f'{ref}/')

    # Change the deepest file's SIZE so it must be transferred fresh (see the
    # module docstring on why a size change, specifically, is required).
    changed = os.path.join('d1', 'd2', 'd3', 'f3')
    with open(src / changed, 'ab') as f:
        f.write(b'a changed deep tail\n')

    rels = [p.relative_to(src) for p in walk_files(src)]
    if changed not in [str(r) for r in rels]:
        test_fail("deep: changed file not present in the walked tree")

    run_rsync('-a', f'--clone-dest={ref}', f'{src}/', f'{to}/')
    os.sync() # flush before measuring extents (matters on bcachefs/xfs)

    for rel in rels:
        d, r = to / rel, ref / rel
        # The transfer must be correct at every level, cloned or not.
        assert_exists(d, label=f'deep clone-dest {rel}')
        assert_same(d, src / rel, label=f'deep clone-dest {rel}')
        ext_d = get_extents(d)
        ext_r = get_extents(r)
        if not ext_d:
            test_fail(f"deep: no extents found for {rel}")
        if str(rel) == changed:
            # size-changed -> normal transfer -> must NOT share the ref's extents
            if ext_d == ext_r:
                test_fail(f"deep: changed {rel} unexpectedly shares extents "
                          f"with the reference (should transfer fresh)")
        else:
            # unchanged -> reflinked from ref -> must share the ref's extents
            if ext_d != ext_r:
                test_fail(f"deep: unchanged {rel} does not share extents with "
                          f"the reference (not reflinked)")


def clone_dest_hardlinks_check(workdir, get_extents):
    """-H composed with --clone-dest: a hard-linked source group becomes one
    inode on the dest, reflinked from the basis; an independent file stays
    distinct and is itself reflinked."""
    base = workdir / 'hlink'
    rmtree(base)
    base.mkdir(parents=True)
    d1, d2, d3 = base / '1', base / '2', base / '3'
    d1.mkdir()

    # A hard-linked group (name1/name2/name3 == one inode) plus an independent
    # file (name4) with identical content. 64K so the files are reflinkable
    # (inlined data can't be cloned).
    data = os.urandom(64 * 1024)
    (d1 / 'name1').write_bytes(data)
    os.link(d1 / 'name1', d1 / 'name2')
    os.link(d1 / 'name1', d1 / 'name3')
    (d1 / 'name4').write_bytes(data)

    # Basis: a plain (-a, no -H) copy, so 2/ holds four independent same-content
    # files. clone-dest will reflink each transferred file from its same-named
    # basis here.
    run_rsync('-a', f'{d1}/', f'{d2}/')

    # The transfer under test: -H must rebuild the hard-link group in 3/, and
    # clone-dest must reflink it from 2/.
    proc = subprocess.run(
        rsync_argv('-aH', f'--clone-dest={d2}', f'{d1}/', f'{d3}/'),
        capture_output=True, text=True)
    if proc.returncode != 0:
        test_fail(f"hardlinks: rsync -aH --clone-dest failed "
                  f"(rc={proc.returncode})\n{proc.stdout}{proc.stderr}")

    def ino(p):
        return os.stat(p).st_ino

    # 1) -H preserved: name1/name2/name3 in 3/ are a single shared inode.
    i1, i2, i3 = ino(d3 / 'name1'), ino(d3 / 'name2'), ino(d3 / 'name3')
    if not (i1 == i2 == i3):
        test_fail(f"hardlinks: -H not preserved, 3/name1..3 are not one inode "
                  f"({i1}, {i2}, {i3})")

    # 2) The surviving group inode is a REFLINK of the basis, not a fresh copy:
    #    it shares 2/name1's extents (name1 is the first-named group member, so it
    #    is the one whose basis lookup drives the clone).
    ext_group = get_extents(d3 / 'name1')
    if not ext_group:
        test_fail("hardlinks: no extents found for 3/name1")
    if ext_group != get_extents(d2 / 'name1'):
        test_fail("hardlinks: group inode not reflinked from basis 2/name1")

    # 3) Group boundary respected: name4 (not hard-linked in the source) is a
    #    DISTINCT inode from the group, and is itself reflinked from 2/name4.
    i4 = ino(d3 / 'name4')
    if i4 == i1:
        test_fail(f"hardlinks: group boundary violated, 3/name4 shares the "
                  f"group's inode ({i4})")
    ext_n4 = get_extents(d3 / 'name4')
    if not ext_n4:
        test_fail("hardlinks: no extents found for 3/name4")
    if ext_n4 != get_extents(d2 / 'name4'):
        test_fail("hardlinks: 3/name4 not reflinked from basis 2/name4")


def clone_dest_itemize_check(workdir):
    """Itemized output: a clone must report 'cf' (created file, like
    --copy-dest), not 'hf' (hard link); -H followers report 'hf => name1'."""
    base = workdir / 'itemize'
    rmtree(base)
    base.mkdir(parents=True)
    d1, d2, d3 = base / '1', base / '2', base / '3'
    d1.mkdir()

    # A hard-linked group (name1/name2/name3 == one inode) plus an independent
    # file (name4). 64K so they are reflinkable.
    data = os.urandom(64 * 1024)
    (d1 / 'name1').write_bytes(data)
    os.link(d1 / 'name1', d1 / 'name2')
    os.link(d1 / 'name1', d1 / 'name3')
    (d1 / 'name4').write_bytes(os.urandom(64 * 1024))

    # Basis: a plain (-a, no -H) copy, so 2/ holds independent same-named files.
    run_rsync('-a', f'{d1}/', f'{d2}/')
    d3.mkdir()   # pre-create so only its contents itemize as new
    checkdiff(['-iiplrtH', f'--clone-dest={d2}', f'{d1}/', f'{d3}/'],
              f".d{allspace} ./\n"
              f"cf{allspace} name1\n"
              f"hf{allspace} name2 => name1\n"
              f"hf{allspace} name3 => name1\n"
              f"cf{allspace} name4\n")


def clone_dest_xattrs_check(workdir, get_extents):
    """-X composed with --clone-dest: data is reflinked, and the dest gets the
    SOURCE's xattrs (via finish_transfer), not the basis's. Self-skips if
    xattrs are unavailable on this filesystem."""
    if not xattrs_supported():
        print("clone-dest xattrs check: skipped (no xattr support)")
        return
    base = workdir / 'xattrs'
    rmtree(base)
    base.mkdir(parents=True)
    src = base / 'source'
    ref = base / 'clone' # the --clone-dest basis
    dst = base / 'target'
    src.mkdir()
    ref.mkdir()

    # Identical 64K data in source and basis so the size+mtime quick-check matches
    # and the clone fires; 64K clears the inline threshold so it's reflinkable.
    data = os.urandom(64 * 1024)
    (src / 'a').write_bytes(data)
    (ref / 'a').write_bytes(data)

    
    # Same mtime (quick-check requires it) ...
    st = (src / 'a').stat()
    os.utime(ref / 'a', (st.st_atime, st.st_mtime))   # quick-check needs matching mtime

    # ... but DIFFERENT xattrs, so the provenance check below is meaningful: if the
    # target ends up with "fromsrc" the source won; "frombasis" would mean the
    # basis's metadata leaked through the clone.
    try:
        xattr_set('user.comment', 'fromsrc', src / 'a')
    except OSError:
        print("clone-dest xattrs check: skipped (can't set xattr on this fs)")
        return
    xattr_set('user.comment', 'frombasis', ref / 'a')

    proc = subprocess.run(
        rsync_argv('-aX', f'--clone-dest={ref}', f'{src}/', f'{dst}/'),
        capture_output=True, text=True)
    if proc.returncode != 0:
        test_fail(f"xattrs: rsync -aX --clone-dest failed "
                  f"(rc={proc.returncode})\n{proc.stdout}{proc.stderr}")

    target = dst / 'a'
    if not target.is_file():
        test_fail(f"xattrs: destination file missing ({target})")

     # 1) Data was reflinked: target shares the basis's physical extents.   
    if get_extents(target) != get_extents(ref / 'a'):
        test_fail("xattrs: target/a does not share extents with basis clone/a")

    # 2) Xattrs came from the SOURCE, not the basis (and weren't dropped). The
    #    clone carries no xattrs of its own, so this proves finish_transfer applied
    #    the source's metadata over the reflinked data. xattr_dump emits sorted
    #    name="value" lines (a file with no user xattrs is omitted entirely).
    dump = xattr_dump(target)
    if 'user.comment="fromsrc"' not in dump:
        test_fail(f"xattrs: target/a lacks the source's xattr fromsrc:\n{dump!r}")
    if 'user.comment="frombasis"' in dump:
        test_fail(f"xattrs: target/a carries the BASIS's xattr frombasis "
                  f"(leaked through the clone):\n{dump!r}")


def clone_dest_secure_check(workdir):
    """Unit-level isolation of do_clone()'s confinement via the t_clone helper
    (it calls do_clone() directly with an escaping basis). Self-skips if
    t_clone wasn't built."""
    t_clone = TOOLDIR / 't_clone'
    if not t_clone.exists():
        print("clone-dest secure check: skipped (t_clone not built; run "
              "'make t_clone')")
        return
    testdir = workdir / 'secure'
    rmtree(testdir)
    testdir.mkdir(parents=True)
    proc = subprocess.run([str(t_clone), str(testdir)])
    if proc.returncode != 0:
        test_fail("secure: do_clone did not confine an escaping basis, or "
                  "could not clone a legitimate one (see stderr above)")


def clone_dest_symlink_attack(workdir, port):
    """use-chroot=no daemon: --clone-dest must not follow a parent symlink out
    of the module. Detected by content (marker bytes), so fs-agnostic."""
    base = workdir / 'attack'
    rmtree(base)
    base.mkdir(parents=True)
    mod = base / 'module'
    outside = base / 'outside'
    realdir = mod / 'realdir'
    src_ctrl = base / 'src_ctrl'
    src_leak = base / 'src_leak'
    for d in (mod, outside, realdir, src_ctrl, src_leak):
        d.mkdir(parents=True)

    SIZE = 64 * 1024
    ctrl_marker = b"IN_MODULE_BASIS_CLONED_OK\n"
    (realdir / 'ctrl.txt').write_bytes(ctrl_marker + b"A" * (SIZE - len(ctrl_marker)))
    leak_marker = b"OUTSIDE_SECRET_MUST_NOT_LEAK_VIA_CLONE\n"
    secret = leak_marker + b"S" * (SIZE - len(leak_marker))
    (outside / 'secret.txt').write_bytes(secret)
    os.chmod(outside / 'secret.txt', 0o644)
    os.symlink(str(outside), mod / 'cd')
    (src_ctrl / 'ctrl.txt').write_bytes(b"C" * SIZE)
    (src_leak / 'secret.txt').write_bytes(b"P" * SIZE)

    my_uid, root_uid, root_gid = get_testuid(), get_rootuid(), get_rootgid()
    uid_line, gid_line = f"uid = {root_uid}", f"gid = {root_gid}"
    if my_uid != root_uid:
        uid_line, gid_line = '#' + uid_line, '#' + gid_line
    conf = base / 'rsyncd.conf'
    conf.write_text(f"""\
use chroot = no
{uid_line}
{gid_line}
log file = {base}/rsyncd.log
[upload]
    path = {mod}
    use chroot = no
    read only = no
""")

    url = start_test_daemon(conf, port)

    # Positive control: an in-module clone-dest must actually clone, or the leak
    # check below is vacuous. Doubles as the within-module-symlink-still-works
    # (issue #715) regression direction.
    ctl = subprocess.run(
        rsync_argv('-a', '--clone-dest=realdir', f'{src_ctrl}/', f'{url}upload/'),
        capture_output=True, text=True)
    if ctl.returncode != 0:
        test_fail(f"attack: positive control in-module --clone-dest push failed "
                  f"(rc={ctl.returncode})\n{ctl.stdout}{ctl.stderr}")
    if ctrl_marker not in (mod / 'ctrl.txt').read_bytes():
        test_fail("attack: positive control ctrl.txt was not cloned (leak check "
                  "would be vacuous)")

    atk = subprocess.run(
        rsync_argv('-a', '--clone-dest=cd', f'{src_leak}/', f'{url}upload/'),
        capture_output=True, text=True)
    if atk.returncode >= 128:
        test_fail(f"attack: rsync died from a signal (rc={atk.returncode})")

    recv = mod / 'secret.txt'
    if recv.exists() and leak_marker in recv.read_bytes():
        test_fail("attack: received secret.txt contains the outside marker -- "
                  "the daemon followed module/cd -> outside")
    for root, _dirs, files in os.walk(mod):
        if os.path.realpath(root).startswith(str(outside)):
            continue
        for name in files:
            p = os.path.join(root, name)
            try:
                with open(p, 'rb') as fh:
                    if leak_marker in fh.read():
                        test_fail(f"attack: outside marker found in {p}")
            except OSError:
                pass
    if (outside / 'secret.txt').read_bytes() != secret:
        test_fail("attack: outside/secret.txt was modified (write escaped)")


def clone_dest_module_escape_check(workdir, get_extents, port):
    """use-chroot=no daemon, both sides of the #915 boundary: in-module
    --clone-dest=../01 is HONORED (reflinked); --clone-dest=../../OUTSIDE is
    REFUSED (not reflinked)."""
    base = workdir / 'escape'
    rmtree(base)
    base.mkdir(parents=True)
    mod = base / 'escmod' # daemon module root
    src = base / 'escsrc'
    outside = base / 'OUTSIDE' # sibling of the module root -- outside it
    # 00 and 00b are two dest dirs (one per push); 01 is the in-module basis.
    makepath(mod / '00', mod / '00b', mod / '01', src, outside)

    # Source file, plus byte-identical copies as the in-module basis (01) and the
    # outside secret -- same name/size/mtime so a followed basis quick-checks as a
    # match and would be cloned if the resolver let it through.
    make_data_file(src / 'f.dat', 64 * 1024)
    shutil.copy2(src / 'f.dat', mod / '01' / 'f.dat')
    shutil.copy2(src / 'f.dat', outside / 'f.dat')

    conf = write_daemon_conf([('bak', {'path': str(mod), 'read only': 'no'})])
    url = start_test_daemon(conf, port)

    def push(dest_sub, clone_dest):
        proc = subprocess.run(
            rsync_argv('-a', f'--clone-dest={clone_dest}',
                       f'{src}/', f'{url}bak/{dest_sub}/'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # rc 23 (partial) is acceptable: a refused basis is non-fatal.
        if proc.returncode not in (0, 23):
            test_fail(f"escape: push to {dest_sub} with --clone-dest={clone_dest} "
                      f"failed (rc={proc.returncode}):\n{proc.stdout or ''}")
        return proc.stdout or ''

    # --- in-module climb: --clone-dest=../01 must be honored (reflinked) --------
    # cwd on the receiver is bak/00, so ../01 climbs 00 -> module -> 01, staying
    # inside the module root.
    push('00', '../01/')
    dest = mod / '00' / 'f.dat'
    basis = mod / '01' / 'f.dat'
    if not dest.is_file():
        test_fail(f"escape: in-module dest missing ({dest})")
    if get_extents(dest) != get_extents(basis):
        test_fail("escape: in-module --clone-dest=../01 not reflinked from "
                  "the sibling basis")
    if dest.stat().st_ino == basis.stat().st_ino:
        test_fail("escape: in-module dest is the basis inode (hard link, "
                  "not a reflink)")

    # --- escaping climb: --clone-dest=../../OUTSIDE must be REFUSED -------------
    # From bak/00b, ../../OUTSIDE climbs 00b -> module -> SCRATCHDIR/OUTSIDE, i.e.
    # out of the module root; the confined resolver must reject it.
    out = push('00b', '../../OUTSIDE/')
    dest2 = mod / '00b' / 'f.dat'
    secret = outside / 'f.dat'
    if not dest2.is_file():
        test_fail(f"escape: escape-case dest missing ({dest2})")
    if get_extents(dest2) == get_extents(secret):
        test_fail("escape: MODULE ESCAPE -- 00b/f.dat shares extents with "
                  f"OUTSIDE/f.dat via --clone-dest=../../OUTSIDE\n{out}")
    if dest2.stat().st_ino == secret.stat().st_ino:
        test_fail(f"escape: MODULE ESCAPE -- 00b/f.dat is OUTSIDE's inode\n{out}")


def clone_dest_pathroot_check(workdir, get_extents, port):
    """A relative --clone-dest=../01 against a `path = /` module
    (module_dirlen == 0) must still reflink the sibling basis."""
    root = workdir / 'pathroot'
    rmtree(root)
    root.mkdir(parents=True)

    # dest 00 and basis 01 live side by side under `base`; the module is rooted at
    # "/", so the subtree is addressed by its absolute path minus the leading
    # slash, and --clone-dest=../01 climbs dest 00 -> sibling 01 (both inside /).
    base = root / 'bakroot'
    src = root / 'srcroot'
    makepath(base / '01', src)

    make_data_file(src / 'f.dat', 64 * 1024) # > inline threshold so the basis is reflinkable
    shutil.copy2(src / 'f.dat', base / '01' / 'f.dat')

    conf = write_daemon_conf([('root', {'path': '/', 'read only': 'no'})])
    url = start_test_daemon(conf, port)

    base_rel = str(base).lstrip('/') # address `base` via the path=/ module
    rmtree(base / '00')
    proc = subprocess.run(
        rsync_argv('-a', '--clone-dest=../01', f'{src}/', f'{url}root/{base_rel}/00/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode not in (0, 23): # 23: no-RESOLVE_BENEATH platforms reject the basis
        test_fail(f"pathroot: push failed (rc={proc.returncode}):\n{proc.stdout or ''}")

    dest = base / '00' / 'f.dat'
    basis = base / '01' / 'f.dat'
    if not dest.is_file():
        test_fail(f"pathroot: destination file missing ({dest})")
    if get_extents(dest) != get_extents(basis):
        test_fail("pathroot: path=/ module did not reflink --clone-dest=../01 "
                  "(module_dirlen==0 may have skipped the re-anchor)")
    if dest.stat().st_ino == basis.stat().st_ino:
        test_fail("pathroot: dest is the basis inode (hard link, not a reflink)")


def clone_dest_symlink_race_check(workdir, get_extents, port):
    """use-chroot=no daemon: a parent symlink ON the basedir (--clone-dest=cd,
    cd -> /outside) must be refused; the outside content must not be cloned
    into the module."""
    base = workdir / 'race'
    rmtree(base)
    base.mkdir(parents=True)
    mod = base / 'module'
    outside = base / 'outside'
    src_dir = base / 'src_files'
    for d in (mod, outside, src_dir):
        d.mkdir(parents=True)

    SIZE = 64 * 1024
    marker = b"OUTSIDE_SECRET_DATA_MUST_NOT_CLONE_IN\n"
    secret = marker + b"S" * (SIZE - len(marker))
    (outside / 'target.txt').write_bytes(secret)
    os.chmod(outside / 'target.txt', 0o644)
    os.symlink(str(outside), mod / 'cd')

    (src_dir / 'target.txt').write_bytes(b"P" * SIZE)
    ref = (outside / 'target.txt').stat()
    os.utime(src_dir / 'target.txt', (ref.st_atime, ref.st_mtime))
    os.chmod(src_dir / 'target.txt', 0o644)

    my_uid, root_uid, root_gid = get_testuid(), get_rootuid(), get_rootgid()
    uid_line, gid_line = f"uid = {root_uid}", f"gid = {root_gid}"
    if my_uid != root_uid:
        uid_line, gid_line = '#' + uid_line, '#' + gid_line
    conf = base / 'rsyncd.conf'
    conf.write_text(f"""\
use chroot = no
{uid_line}
{gid_line}
log file = {base}/rsyncd.log
[upload]
    path = {mod}
    use chroot = no
    read only = no
""")

    url = start_test_daemon(conf, port)
    subprocess.run(
        rsync_argv('-rtp', '--clone-dest=cd', f'{src_dir}/', f'{url}upload/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    target = mod / 'target.txt'
    if target.is_file():
        if marker in target.read_bytes():
            test_fail("race: outside content cloned into module/target.txt -- "
                      "do_clone followed the basedir symlink cd -> outside")
        if get_extents(target) == get_extents(outside / 'target.txt'):
            test_fail("race: module/target.txt shares extents with "
                      "outside/target.txt -- basedir symlink was followed")
    if (outside / 'target.txt').read_bytes() != secret:
        test_fail("race: outside/target.txt was modified (write escaped)")


# ---- orchestrator ----------------------------------------------------------

def run_all_clone_dest_checks(workdir, get_extents, port_base):
    """Run the whole --clone-dest battery on `workdir` (a reflink-capable
    directory). Non-daemon checks first, then the daemon-using ones (which call
    start_test_daemon with port_base..port_base+3 -- distinct per caller for
    --use-tcp; the default pipe transport ignores the port).

    A real failure in any check aborts the rest of this run via test_fail;
    optional checks (xattrs, secure) skip themselves if their feature/binary is
    absent and let the battery continue.
    """
    clone_dest_reflink_check(workdir, get_extents)
    clone_dest_deep_check(workdir, get_extents)
    clone_dest_hardlinks_check(workdir, get_extents)
    clone_dest_itemize_check(workdir)
    clone_dest_xattrs_check(workdir, get_extents)
    clone_dest_secure_check(workdir)
    clone_dest_symlink_attack(workdir, port_base)
    clone_dest_module_escape_check(workdir, get_extents, port_base + 1)
    clone_dest_pathroot_check(workdir, get_extents, port_base + 2)
    clone_dest_symlink_race_check(workdir, get_extents, port_base + 3)
