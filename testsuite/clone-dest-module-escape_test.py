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

from rsyncfns import (
    SCRATCHDIR,
    test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink, clone_dest_module_escape_check

DAEMON_PORT = 12917

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_module_escape_check(SCRATCHDIR, filefrag_extents, DAEMON_PORT)

print("clone-dest-module-escape: checked --clone-dest does not escape root directory")
