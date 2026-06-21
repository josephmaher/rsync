#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Filesystem-agnostic --clone-dest test. Makes no filesystem and needs no root:
# it probes whether the filesystem the scratch dir already lives on supports
# reflinks (FICLONE, via cp --reflink=always), and if so runs the reflink check
# to check that --clone-dest is creating files with shared extents.

import platform
import shutil

from rsyncfns import SCRATCHDIR, rmtree, test_skipped
from clone_dest_lib import (
    clone_dest_reflink_check, filefrag_extents, supports_reflink,
)

DAEMON_PORT = 12887

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('cp'):
    test_skipped("can't find cp")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")


work = SCRATCHDIR / 'agnostic'
rmtree(work)
work.mkdir(parents=True)

if not supports_reflink(work):
    test_skipped(f"filesystem under {work} does not support reflinks")

clone_dest_reflink_check(work, filefrag_extents)

print("clone-dest: check extents are shared passed")
