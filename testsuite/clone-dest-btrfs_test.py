#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Make a btrfs filesystem and run the --clone-dest reflink and symlink-attack
# checks on it. The shared bodies live in clone_dest_lib.py.

import atexit
import platform
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, test_skipped
from clone_dest_lib import (
    clone_dest_reflink_check, clone_dest_symlink_attack, filefrag_extents,
)

DAEMON_PORT = 12884  # distinct per fs test for --use-tcp mode

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux + btrfs (FICLONE)")
if not shutil.which('mkfs.btrfs'):
    test_skipped("can't find mkfs.btrfs (Linux btrfs-progs)")
if not shutil.which('filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

image = SCRATCHDIR / 'btrfs.image'
mnt = SCRATCHDIR / 'mnt'
mnt.mkdir(parents=True, exist_ok=True)

# 256M stays clear of the small-image thresholds that flip mkfs.btrfs into
# mixed mode or make it fail on some versions.
subprocess.run(['truncate', '-s', '256M', str(image)], check=True)
if subprocess.run(['mkfs.btrfs', '-q', str(image)], capture_output=True).returncode != 0:
    test_skipped("mkfs.btrfs failed")
if subprocess.run(['mount', '-o', 'loop', str(image), str(mnt)],
                  capture_output=True).returncode != 0:
    test_skipped("can't mount btrfs image, try running as root")

# Register umount BEFORE start_test_daemon runs (inside the attack check):
# atexit fires handlers in reverse order, so the daemon's cleanup runs first
# and releases the mount before we umount it.
def _umount():
    if subprocess.run(['umount', str(mnt)], capture_output=True).returncode != 0:
        subprocess.run(['umount', '-l', str(mnt)], capture_output=True)
atexit.register(_umount)

clone_dest_reflink_check(mnt, filefrag_extents)
clone_dest_symlink_attack(mnt, DAEMON_PORT)
