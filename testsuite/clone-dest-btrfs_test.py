#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Make a btrfs filesystem and run the full --clone-dest battery on it.
# All check bodies live in clone_dest_lib.py.

import atexit
import platform
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, test_skipped
from clone_dest_lib import (
    run_all_clone_dest_checks, filefrag_extents, supports_reflink,
)

DAEMON_PORT_BASE = 12840   # uses 12840..12843; distinct per fs for --use-tcp

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux + btrfs (FICLONE)")
if not shutil.which('mkfs.btrfs'):
    test_skipped("can't find mkfs.btrfs (Linux btrfs-progs)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

image = SCRATCHDIR / 'btrfs.image'
mnt = SCRATCHDIR / 'mnt'
mnt.mkdir(parents=True, exist_ok=True)

# 256M stays clear of the small-image thresholds that flip mkfs.btrfs into
# mixed mode or make it fail on some versions; also comfortably fits the whole
# battery's files.
subprocess.run(['truncate', '-s', '256M', str(image)], check=True)
if subprocess.run(['mkfs.btrfs', '-q', str(image)], capture_output=True).returncode != 0:
    test_skipped("mkfs.btrfs failed")
if subprocess.run(['mount', '-o', 'loop', str(image), str(mnt)],
                  capture_output=True).returncode != 0:
    test_skipped("can't mount btrfs image, try running as root")

# Register umount BEFORE any daemon starts (inside the checks): atexit fires in
# reverse order, so daemon cleanup runs first and releases the mount.
def _umount():
    if subprocess.run(['umount', str(mnt)], capture_output=True).returncode != 0:
        subprocess.run(['umount', '-l', str(mnt)], capture_output=True)
atexit.register(_umount)

if not supports_reflink(mnt):
    test_skipped("mounted btrfs image does not support reflinks")

run_all_clone_dest_checks(mnt, filefrag_extents, DAEMON_PORT_BASE)
