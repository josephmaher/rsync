#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Make a bcachefs filesystem and run the full --clone-dest battery on it.
# All check bodies live in clone_dest_lib.py.

import atexit
import platform
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, test_skipped
from clone_dest_lib import (
    run_all_clone_dest_checks, filefrag_extents, supports_reflink,
)

DAEMON_PORT_BASE = 12850   # uses 12850..12853

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('mkfs.bcachefs'):
    test_skipped("can't find mkfs.bcachefs (Linux bcachefs-tools)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

image = SCRATCHDIR / 'bcachefs.image'
mnt = SCRATCHDIR / 'mnt'
mnt.mkdir(parents=True, exist_ok=True)

# 100M: the old 25M sufficed for just two checks, but the full battery creates
# more files; 100M gives comfortable headroom. mkfs failure just skips.
subprocess.run(['truncate', '-s', '100M', str(image)], check=True)
if subprocess.run(['mkfs.bcachefs', str(image)], capture_output=True).returncode != 0:
    test_skipped("mkfs.bcachefs failed")
if subprocess.run(['mount', '-o', 'loop', str(image), str(mnt)],
                  capture_output=True).returncode != 0:
    test_skipped("can't mount bcachefs image, try running as root")

def _umount():
    if subprocess.run(['umount', str(mnt)], capture_output=True).returncode != 0:
        subprocess.run(['umount', '-l', str(mnt)], capture_output=True)
atexit.register(_umount)

if not supports_reflink(mnt):
    test_skipped("mounted bcachefs image does not support reflinks")

run_all_clone_dest_checks(mnt, filefrag_extents, DAEMON_PORT_BASE)
