#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Make an XFS filesystem (reflink=1) and run the full --clone-dest battery on
# it. All check bodies live in clone_dest_lib.py. XFS verifies extent sharing
# with its native xfs_bmap.

import atexit
import platform
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, test_skipped
from clone_dest_lib import (
    run_all_clone_dest_checks, xfs_bmap_extents, supports_reflink,
)

DAEMON_PORT_BASE = 12860   # uses 12860..12863

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('mkfs.xfs'):
    test_skipped("can't find mkfs.xfs (Linux xfsprogs)")
if not shutil.which('xfs_bmap'):
    test_skipped("can't find xfs_bmap (Linux xfsprogs)")

image = SCRATCHDIR / 'xfs.image'
mnt = SCRATCHDIR / 'mnt'
mnt.mkdir(parents=True, exist_ok=True)

# 300M clears XFS's minimum size. -f overwrites any stale signature; -m
# reflink=1 is required for clone support and turns "XFS without reflink" into
# a clean skip (mkfs fails on too-old xfsprogs).
subprocess.run(['truncate', '-s', '300M', str(image)], check=True)
if subprocess.run(['mkfs.xfs', '-f', '-m', 'reflink=1', str(image)],
                  capture_output=True).returncode != 0:
    test_skipped("mkfs.xfs failed (no reflink support, or image too small?)")
if subprocess.run(['mount', '-o', 'loop', str(image), str(mnt)],
                  capture_output=True).returncode != 0:
    test_skipped("can't mount xfs image, try running as root")

def _umount():
    if subprocess.run(['umount', str(mnt)], capture_output=True).returncode != 0:
        subprocess.run(['umount', '-l', str(mnt)], capture_output=True)
atexit.register(_umount)

if not supports_reflink(mnt):
    test_skipped("mounted xfs image does not support reflinks")

run_all_clone_dest_checks(mnt, xfs_bmap_extents, DAEMON_PORT_BASE)
