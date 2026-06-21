#!/usr/bin/env python3
# --clone-dest composed with -H (hard-link preservation).
#
# Clone analogue of the "-aH --link-dest" line in hardlinks_test.py. -H and
# --clone-dest pull on different primitives -- -H wants several names to share
# one inode, a reflink gives a *distinct* inode -- so their composition is a
# real seam worth pinning. The good outcome (confirmed on btrfs): a hard-linked
# source group becomes ONE inode on the destination (so -H is preserved) whose
# extents are SHARED with the basis (so it was reflinked, not freshly copied).
#
# How it composes in try_dests_reg: the group's first member (by name) gets the
# clone-dest basis lookup and is reflinked; the remaining members are
# hard-linked to that first dest file by finish_hard_link. So the group reflinks
# iff the FIRST-named member has a same-named basis in the clone-dest (alt-dest
# matches by path, not content) -- which is why the basis here is a full copy.
#
# Needs a reflink-capable scratch filesystem (FICLONE); skips otherwise.

import platform
import shutil

from rsyncfns import (
    SCRATCHDIR,
    test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink, clone_dest_hardlinks_check

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_hardlinks_check(SCRATCHDIR, filefrag_extents)

print("clone-dest-hardlinks: -H group preserved and reflinked from the basis")
