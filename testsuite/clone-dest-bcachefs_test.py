#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Make a bcachefs filesystem and run the --clone-dest reflink and
# symlink-attack checks on it. Shared bodies live in clone_dest_lib.py.

import atexit
import platform
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, test_skipped
from clone_dest_lib import (
    clone_dest_reflink_check, clone_dest_symlink_attack, filefrag_extents,
)

DAEMON_PORT = 12885

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('mkfs.bcachefs'):
    test_skipped("can't find mkfs.bcachefs (Linux bcachefs-tools)")
if not shutil.which('filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

image = SCRATCHDIR / 'bcachefs.image'
mnt = SCRATCHDIR / 'mnt'
mnt.mkdir(parents=True, exist_ok=True)

# 25M is the size the original test used; bcachefs tolerates a much smaller
# image than btrfs. mkfs failure (e.g. a version rejecting it) just skips.
subprocess.run(['truncate', '-s', '25M', str(image)], check=True)
if subprocess.run(['mkfs.bcachefs', str(image)], capture_output=True).returncode != 0:
    test_skipped("mkfs.bcachefs failed")
if subprocess.run(['mount', '-o', 'loop', str(image), str(mnt)],
                  capture_output=True).returncode != 0:
    test_skipped("can't mount bcachefs image, try running as root")

def _umount():
    if subprocess.run(['umount', str(mnt)], capture_output=True).returncode != 0:
        subprocess.run(['umount', '-l', str(mnt)], capture_output=True)
atexit.register(_umount)

# clone_dest_reflink_check syncs before measuring, which is what bcachefs needs
# (it caches extents); no bcachefs-specific handling is required here.
clone_dest_reflink_check(mnt, filefrag_extents)
clone_dest_symlink_attack(mnt, DAEMON_PORT)
