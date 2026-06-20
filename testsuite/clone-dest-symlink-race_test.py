#!/usr/bin/env python3
# Clone-dest analogue of alt-dest-symlink-race_test.py (which covers
# --link-dest / --copy-dest / --compare-dest).
#
# Targets the *basedir-confinement* path specifically: a parent symlink ON the
# alt-dest basedir. In a use-chroot=no daemon a local attacker who can write
# into a module plants module/cd -> /outside and then pushes with
# --clone-dest=cd. If do_clone's basedir resolution followed that symlink, the
# receiver would reflink /outside/target.txt into the client-readable module
# (read disclosure). do_clone resolves the basedir through
# secure_relative_open(), which RESOLVE_BENEATH rejects, so the outside content
# must never appear in the module.
#
# This is a SYSTEM-level basedir-escape test. Like the daemon attack in
# clone_dest_lib, a *static* planted symlink may be caught by the basis lookup
# (link_stat in try_dests_reg) before do_clone is reached; either layer
# refusing is a correct outcome. The unit-level isolation of do_clone's
# confinement lives in clone-dest-secure_test.py / t_clone.
#
# Detection is by content/extents, not inode: a clone produces a *different*
# inode but *shared* extents, so the link-dest inode check is replaced by a
# filefrag extent comparison (and a marker-content check).
#
# Needs a reflink-capable scratch filesystem (FICLONE); skips otherwise.

import platform
import shutil

from rsyncfns import (
    SCRATCHDIR,
    test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink, clone_dest_symlink_attack

DAEMON_PORT = 12888  # distinct from the other daemon tests

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_symlink_attack(SCRATCHDIR, DAEMON_PORT)

print("clone-dest-symlink-race: check passed")
