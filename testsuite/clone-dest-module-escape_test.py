#!/usr/bin/env python3
# Clone-dest analogue of link-dest-module-escape_test.py: pins BOTH sides of
# the #915 module boundary for --clone-dest in a use-chroot=no daemon.
#
#   in-module climb  (--clone-dest=../01):  HONORED -- dest is reflinked from
#       the sibling basis (same as link-dest re-permitting an in-module `..`).
#   escaping climb   (--clone-dest=../../OUTSIDE): REFUSED -- the basis points
#       outside the module root, so it must never be used; the dest is
#       re-transferred and does NOT share the outside file's extents (a shared
#       clone would be a cross-module info leak).
#
# Detection is by EXTENTS, not inode: a clone is always a distinct inode from
# its basis, so inode identity (link-dest's signal) can't tell "reflinked" from
# "re-transferred" -- shared physical extents is the reflink signal.
#
# The resolver confines beneath module_dir with RESOLVE_BENEATH (openat2 /
# O_RESOLVE_BENEATH), or rejects the `..` outright via the portable resolver,
# so the escape is blocked on every platform. Needs a reflink-capable scratch
# filesystem (FICLONE); skips otherwise. Runs at any uid.

import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)
from clone_dest_lib import filefrag_extents, supports_reflink

DAEMON_PORT = 12917
DATA_SIZE = 64 * 1024   # > inline threshold so the basis is reflinkable

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

mod = SCRATCHDIR / 'escmod'          # daemon module root
src = SCRATCHDIR / 'escsrc'
outside = SCRATCHDIR / 'OUTSIDE'     # sibling of the module root -- OUTSIDE it
for d in (mod, src, outside):
    rmtree(d)
# 00 and 00b are two dest dirs (one per push); 01 is the in-module basis.
makepath(mod / '00', mod / '00b', mod / '01', src, outside)

if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

# Source file, plus byte-identical copies as the in-module basis (01) and the
# outside secret -- same name/size/mtime so a followed basis quick-checks as a
# match and would be cloned if the resolver let it through.
make_data_file(src / 'f.dat', DATA_SIZE)
shutil.copy2(src / 'f.dat', mod / '01' / 'f.dat')
shutil.copy2(src / 'f.dat', outside / 'f.dat')

conf = write_daemon_conf([
    ('bak', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def push(dest_sub, clone_dest):
    proc = subprocess.run(
        rsync_argv('-a', f'--clone-dest={clone_dest}', f'{src}/', f'{url}bak/{dest_sub}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # rc 23 (partial) is acceptable: a refused basis is non-fatal.
    if proc.returncode not in (0, 23):
        test_fail(f"push to {dest_sub} with --clone-dest={clone_dest} failed "
                  f"unexpectedly (rc={proc.returncode}):\n{proc.stdout or ''}")
    return proc.stdout or ''


# --- in-module climb: --clone-dest=../01 must be HONORED (reflinked) --------
# cwd on the receiver is bak/00, so ../01 climbs 00 -> module -> 01, staying
# inside the module root.
push('00', '../01/')
dest = mod / '00' / 'f.dat'
basis = mod / '01' / 'f.dat'
if not dest.is_file():
    test_fail(f"in-module: destination file missing ({dest})")
if filefrag_extents(dest) != filefrag_extents(basis):
    test_fail("in-module climb not honored: bak/00/f.dat does not share extents "
              "with the sibling basis bak/01/f.dat (--clone-dest=../01 was not "
              "reflinked)")
if dest.stat().st_ino == basis.stat().st_ino:
    test_fail("bak/00/f.dat has the SAME inode as the basis -- that's a hard "
              "link, not a reflink (clone should be a distinct inode)")

# --- escaping climb: --clone-dest=../../OUTSIDE must be REFUSED -------------
# From bak/00b, ../../OUTSIDE climbs 00b -> module -> SCRATCHDIR/OUTSIDE, i.e.
# out of the module root; the confined resolver must reject it.
out = push('00b', '../../OUTSIDE/')
dest2 = mod / '00b' / 'f.dat'
secret = outside / 'f.dat'
if not dest2.is_file():
    test_fail(f"escape: destination file missing ({dest2})")
if filefrag_extents(dest2) == filefrag_extents(secret):
    test_fail(
        "MODULE ESCAPE: bak/00b/f.dat shares extents with OUTSIDE/f.dat via "
        "--clone-dest=../../OUTSIDE -- the confined resolver let a `..` climb "
        f"escape the module root and reflink an outside file in.\n{out}")
if dest2.stat().st_ino == secret.stat().st_ino:
    test_fail(f"MODULE ESCAPE: bak/00b/f.dat is the OUTSIDE file's inode.\n{out}")

print("clone-dest-module-escape: in-module ../01 reflinked, ../../OUTSIDE refused")
