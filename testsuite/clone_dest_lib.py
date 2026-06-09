#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Shared helpers for the --clone-dest reflink tests. This is NOT a test: the
# filename doesn't end in "test.py", so runtests.py won't run it. Each
# clone-dest-<fs>_test.py makes a reflink-capable filesystem (or, for the
# agnostic clone-dest_test.py, probes the one the scratch dir is on) and then
# calls the two checks below against it.

import filecmp
import os
import re
import subprocess

from rsyncfns import (
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail,
)


# ---- extent reporters: pass the right one to clone_dest_reflink_check ----

_FILEFRAG_RE = re.compile(r'^\s*\d+:')

def filefrag_extents(path):
    """Physical-offset start of every data extent, via filefrag (FIEMAP).

    Filesystem-agnostic. -s forces a sync so delalloc is flushed and offsets
    are stable; we pull the physical-offset start column from each numbered
    extent row (turning '..' into a field break first).
    """
    out = subprocess.run(['/sbin/filefrag', '-s', '-v', str(path)],
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
    Used by the agnostic and deep tests to skip on non-reflink filesystems.
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


# ---- the two checks ----

def clone_dest_reflink_check(workdir, get_extents):
    """--clone-dest must reflink an unchanged file, and only that file.

    `workdir` must live on a reflink-capable filesystem. `get_extents` is the
    extent reporter used to prove sharing (filefrag_extents or
    xfs_bmap_extents).
    """
    base = workdir / 'reflink'
    rmtree(base)
    base.mkdir(parents=True)
    d1, d2, d3 = base / '1', base / '2', base / '3'
    for d in (d1, d2, d3):
        d.mkdir()

    # Identical bytes in separate files: same content, independent extents (no
    # filesystem here auto-dedups), so the checks below can tell a clone from a
    # copy. 64K clears any inline-data threshold.
    data = os.urandom(64 * 1024)
    (d1 / 'a').write_bytes(data)
    (d1 / 'b').write_bytes(data)
    # 3/a: identical content to 1/a, different mtime -- fine, --clone-dest
    # clones the data regardless of attribute match and sets attrs from source.
    (d3 / 'a').write_bytes(data)
    os.sync()  # harmless everywhere; bcachefs/xfs need data flushed before measuring

    clonedir = os.path.realpath(d3)
    proc = subprocess.run(
        rsync_argv('-a', f'--clone-dest={clonedir}', f'{d1}/', f'{d2}/'),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        test_fail(f"rsync --clone-dest failed (rc={proc.returncode})\n{proc.stdout}{proc.stderr}")
    os.sync()

    for name in ('a', 'b'):
        if not filecmp.cmp(d1 / name, d2 / name, shallow=False):
            test_fail(f"2/{name} content differs from 1/{name} after transfer")

    ext_2a = get_extents(d2 / 'a')
    ext_3a = get_extents(d3 / 'a')
    ext_2b = get_extents(d2 / 'b')

    # Generic delalloc guard: a no-op for filefrag's numeric tokens, a real
    # guard for xfs_bmap (two unflushed files would compare equal -> false pass).
    if any('delalloc' in t for t in ext_2a + ext_3a + ext_2b):
        test_fail("extent reporter shows delalloc (data not flushed); "
                  "comparison would be unreliable")

    # 2/a was built from the clone-dest, so it must share 3/a's physical extents.
    if not ext_2a:
        test_fail("no extents found for 2/a (inlined, or extent output not parsed?)")
    if ext_2a != ext_3a:
        test_fail("clone-dest file 2/a does not share extents with 3/a")

    # A reflink shares extents but is a DISTINCT inode -- this is what separates
    # --clone-dest from --link-dest (a hard link shares extents too, because it's
    # the same inode, so the extent check alone can't tell them apart).
    if os.stat(d2 / 'a').st_ino == os.stat(d3 / 'a').st_ino:
        test_fail("clone-dest hard-linked 2/a to the basis instead of reflinking it")

    # 2/b has no counterpart in the clone-dest, so rsync must have copied it
    # normally: it must NOT share 3/a's extents, even though the data matches.
    if not ext_2b:
        test_fail("no extents found for 2/b")
    if ext_2b == ext_3a:
        test_fail("2/b unexpectedly shares extents with the clone-dest")


def clone_dest_symlink_attack(workdir, port):
    """In a use-chroot=no daemon, --clone-dest must not follow a parent symlink
    out of the module (read disclosure / write escape).

    The leak is detected by content (marker bytes), so this is itself
    filesystem-agnostic; `workdir` only needs to be reflink-capable so the
    clone path is actually exercised. Runs unprivileged in the default (stdio)
    daemon transport; `port` is used only under --use-tcp.
    """
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
    # The escaping symlink the attacker plants inside the module.
    os.symlink(str(outside), mod / 'cd')
    # What the client pushes: distinct content, identical sizes. If protection
    # holds, the received files contain THIS, never the basis/secret bytes.
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
        capture_output=True, text=True,
    )
    if ctl.returncode != 0:
        test_fail(f"positive control: in-module --clone-dest push failed "
                  f"(rc={ctl.returncode})\n{ctl.stdout}{ctl.stderr}")
    if ctrl_marker not in (mod / 'ctrl.txt').read_bytes():
        test_fail("positive control: ctrl.txt was not cloned from the in-module "
                  "basis (clone path inactive; leak check would be vacuous)")

    # Attack: the escaping clone-dest must not leak outside content into the
    # module, whether rsync fell back to a normal copy or aborted.
    atk = subprocess.run(
        rsync_argv('-a', '--clone-dest=cd', f'{src_leak}/', f'{url}upload/'),
        capture_output=True, text=True,
    )
    if atk.returncode >= 128:
        test_fail(f"attack push: rsync died from a signal (rc={atk.returncode})")

    recv = mod / 'secret.txt'
    if recv.exists() and leak_marker in recv.read_bytes():
        test_fail("clone-dest leak: received secret.txt contains the outside "
                  "marker -- the daemon followed module/cd -> outside")
    for root, _dirs, files in os.walk(mod):
        if os.path.realpath(root).startswith(str(outside)):
            continue  # don't descend through the planted symlink into outside/
        for name in files:
            p = os.path.join(root, name)
            try:
                with open(p, 'rb') as fh:
                    if leak_marker in fh.read():
                        test_fail(f"clone-dest leak: outside marker found in {p}")
            except OSError:
                pass
    if (outside / 'secret.txt').read_bytes() != secret:
        test_fail("outside/secret.txt was modified -- write escaped the module")
