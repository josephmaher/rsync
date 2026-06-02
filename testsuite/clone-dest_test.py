#!/usr/bin/env python3
# Copyright (C) 2024 by Joseph Maher <github@josephmaher.org>
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# Filesystem-agnostic --clone-dest test. Makes no filesystem and needs no root:
# it probes whether the filesystem the scratch dir already lives on supports
# reflinks (FICLONE, via cp --reflink=always), and if so runs the reflink and
# symlink-attack checks right there. Skips on a non-reflink fs (ext4, tmpfs).
# This is the one clone-dest test that runs in ordinary unprivileged CI.

import os
import platform
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, test_skipped
from clone_dest_lib import (
    clone_dest_reflink_check, clone_dest_symlink_attack, filefrag_extents,
)

DAEMON_PORT = 12887

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('cp'):
    test_skipped("can't find cp")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")


def supports_reflink(directory):
    """True if `directory`'s filesystem can make a reflink.

    Probes with cp --reflink=always on a file above the inline-data threshold.
    cp computes the arch-correct FICLONE ioctl number for us, so this is more
    portable than a hardcoded ioctl constant.
    """
    src = directory / '.reflink_probe.src'
    dst = directory / '.reflink_probe.dst'
    try:
        src.write_bytes(os.urandom(64 * 1024))
        return subprocess.run(['cp', '--reflink=always', str(src), str(dst)],
                              capture_output=True).returncode == 0
    except OSError:
        return False
    finally:
        for p in (src, dst):
            try:
                p.unlink()
            except OSError:
                pass


work = SCRATCHDIR / 'agnostic'
rmtree(work)
work.mkdir(parents=True)

if not supports_reflink(work):
    test_skipped(f"filesystem under {work} does not support reflinks")

clone_dest_reflink_check(work, filefrag_extents)
clone_dest_symlink_attack(work, DAEMON_PORT)
