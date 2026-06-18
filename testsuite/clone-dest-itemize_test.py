#!/usr/bin/env python3
# Itemized-output (-i / -ii) check for --clone-dest.
#
# Clone analogue of the --copy-dest / --link-dest variant blocks in
# itemize_test.py. The point is the per-file change code: a cloned file is a
# NEW inode whose data is shared with the basis, so it must itemize like
# --copy-dest ("cf" -- created file), NOT like --link-dest ("hf" -- hard
# link). An earlier version of the patch reused link-dest's itemize call and
# mislabeled reflinks as "hf"; this test locks the corrected "cf".
#
# It also pins the -H composition: when a hard-linked group is cloned, the
# first member is the clone ("cf") and the remaining members are hard-linked
# to it ("hf => firstmember") -- the same as link-dest for the followers,
# since hard-linking subsequent group members is identical either way.
#
# Needs a reflink-capable scratch filesystem (FICLONE); skips otherwise.

import os
import platform
import shutil

from rsyncfns import (
    SCRATCHDIR,
    all_plus, allspace, dots,
    checkdiff, rmtree, run_rsync, test_skipped,
)
from clone_dest_lib import supports_reflink, clone_dest_itemize_check

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_itemize_check(SCRATCHDIR)

print("clone-test-itemize: checked output of -i / -ii for --clone-dest")
