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
from clone_dest_lib import filefrag_extents, supports_reflink, clone_dest_pathroot_check

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

DAEMON_PORT = 12932

clone_dest_pathroot_check(SCRATCHDIR, filefrag_extents, DAEMON_PORT)

print("clone-dest-pathroot: path=/ module honored --clone-dest=../01 (reflinked)")
