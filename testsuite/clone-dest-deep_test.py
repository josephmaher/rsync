#!/usr/bin/env python3
"""--clone-dest at depth and across directory boundaries.

Companion to alt-dest-deep_test.py (which covers --link-dest / --copy-dest /
--compare-dest). Asserts the distinguishing property of --clone-dest at every
level of a >=3-deep tree, with the reference tree placed OUTSIDE both the
source and destination trees (a sibling, not a parent/child):

  unchanged files are REFLINKED from the reference -- a *different* inode but
                 SHARED physical extents (not a hard link);
  a size-changed file is transferred fresh -- INDEPENDENT extents.

This drives the two-dirfd / outside-tree path resolution that do_clone's
confinement rewrites, at depth. Unlike alt-dest-deep_test.py it needs a
reflink-capable scratch filesystem (skips otherwise) and filefrag to verify
sharing, but it still runs unprivileged.

Note the asymmetry with --link-dest: clone-dest only reflinks when the
quick-check (size+mtime) passes, so the changed file below must differ in
SIZE to fall through to a normal transfer. A same-size, same-mtime change
would be cloned from the now-stale basis -- a known property of clone-dest,
not exercised here.
"""

import os
import platform
import shutil

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_exists, assert_same, make_tree, rmtree, run_rsync, walk_files,
    test_fail, test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink, clone_dest_deep_check

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_deep_check(SCRATCHDIR, filefrag_extents)

print("clone-dest-deep: reflink sharing verified at depth")
