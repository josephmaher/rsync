#!/usr/bin/env python3
# Unit test for do_clone()'s symlink-race-safe confinement, via the t_clone
# helper. Companion to secure-relpath-validation_test.py.
#
# Unlike the clone-dest*_test.py integration tests -- which only ever reach
# do_clone() with legitimate within-tree paths, so they pass even with the old
# unconfined do_clone() (the basis lookup catches static attacks first) -- this
# calls do_clone() DIRECTLY with an escaping basis. It therefore isolates
# do_clone's own confinement: it fails on the old path-based do_clone() and
# passes on the confined one.
#
# do_clone() issues FICLONE for the positive (legitimate-basis) case, so this
# needs a reflink-capable scratch filesystem and skips otherwise.
#
# The helper program t_clone is linked at build time with the specific rsync
# binary being built.  Running runtests.py with the --rsync-bin flag will not
# change this, t_clone must be rebuilt with the other version of rsync.

import platform

from rsyncfns import SCRATCHDIR, test_skipped
from clone_dest_lib import supports_reflink, clone_dest_secure_check

if platform.system() != 'Linux':
    test_skipped("do_clone reflinks require Linux (FICLONE)")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_secure_check(SCRATCHDIR)

print("clone-dest-secure: check passed")
