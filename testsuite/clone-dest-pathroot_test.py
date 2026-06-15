#!/usr/bin/env python3
# Functional check: a relative --clone-dest=../01 against a daemon module with
# `path = /` (module_dirlen == 0) -- the #897 x #915 intersection.
#
# The #915 re-anchor (resolve the receiver's basis beneath the module root, so
# an in-module "../01" climb is honored) was gated on a nonzero module_dirlen,
# and a `path = /` module has module_dirlen == 0, so the gate could skip the
# re-anchor and silently ignore --link-dest=../01 there. This pins that
# clone-dest does NOT have that blind spot: with a path=/ module, --clone-dest=
# ../01 must reflink the sibling basis (shared extents, distinct inode).
#
# Unlike the link-dest version this is a PASS test, not XFAIL: clone-dest is
# new code built on the already-fixed re-anchor, so the behavior works rather
# than tracking an open regression.
#
# Detection is by EXTENTS, not inode: a clone is always a distinct inode from
# its basis, so inode identity (link-dest's signal) can't distinguish
# "reflinked" from "re-transferred". Needs a reflink-capable scratch fs
# (FICLONE); skips otherwise. Runs at any uid.

import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)
from clone_dest_lib import filefrag_extents, supports_reflink

DAEMON_PORT = 12932
DATA_SIZE = 64 * 1024   # > inline threshold so the basis is reflinkable

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

# dest 00 and basis 01 live side by side under `base`; the module is rooted at
# "/", so the subtree is addressed by its absolute path minus the leading
# slash, and --clone-dest=../01 climbs dest 00 -> sibling 01 (both inside /).
base = SCRATCHDIR / 'bakroot'
src = SCRATCHDIR / 'srcroot'
rmtree(base)
rmtree(src)
makepath(base / '01', src)

if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

make_data_file(src / 'f.dat', DATA_SIZE)
shutil.copy2(src / 'f.dat', base / '01' / 'f.dat')

conf = write_daemon_conf([
    ('root', {'path': '/', 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

base_rel = str(base).lstrip('/')          # address `base` via the path=/ module
rmtree(base / '00')
proc = subprocess.run(
    rsync_argv('-a', '--clone-dest=../01', f'{src}/', f'{url}root/{base_rel}/00/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
if proc.returncode not in (0, 23):    # 23: no-RESOLVE_BENEATH platforms reject the basis
    test_fail(f"path=/ --clone-dest push failed unexpectedly (rc={proc.returncode}):\n{out}")

dest = base / '00' / 'f.dat'
basis = base / '01' / 'f.dat'
if not dest.is_file():
    test_fail(f"destination file missing ({dest})")

if filefrag_extents(dest) != filefrag_extents(basis):
    test_fail(
        "#897/#915 (path=/ case): --clone-dest=../01 did not reflink the "
        "sibling basis under a `path = /` module (module_dirlen==0 may have "
        f"skipped the re-anchor); the file was re-transferred.\n{out}")
if dest.stat().st_ino == basis.stat().st_ino:
    test_fail(f"{dest} has the same inode as the basis -- hard link, not a "
              f"reflink (clone should be a distinct inode).\n{out}")

print("clone-dest-pathroot: path=/ module honored --clone-dest=../01 (reflinked)")
