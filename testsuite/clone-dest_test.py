#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Filesystem-agnostic --clone-dest test. Makes no filesystem and needs no root:
# it probes whether the filesystem the scratch dir already lives on supports
# reflinks (FICLONE, via cp --reflink=always), and if so runs the reflink and
# symlink-attack checks right there. Skips on a non-reflink fs (ext4, tmpfs).
# This is the one clone-dest test that runs in ordinary unprivileged CI.

import platform
import shutil

from rsyncfns import SCRATCHDIR, rmtree, test_skipped
from clone_dest_lib import (
    clone_dest_reflink_check, clone_dest_symlink_attack, filefrag_extents,
    supports_reflink,
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
clone_dest_symlink_attack(work, DAEMON_PORT)
