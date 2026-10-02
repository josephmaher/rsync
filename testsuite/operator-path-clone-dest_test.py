#!/usr/bin/env python3
# --clone-dest symlink policy matrix.  A followed --clone-dest symlink lets the
# basis lookup read an out-of-tree directory and REFLINK dest files from it.  The
# operator-path ownership walk must follow only uid0/euid-owned symlinks (abs and
# rel, leaf and parent); --insecure-links is the local opt-out.
#
# Clone analogue of operator-path-link-dest_test.py; the matrix (ownership,
# abs/rel, leaf/parent, opt-out) is driven by run_symlink_matrix, and this only
# supplies the "was it followed?" detector.  Two clone-specific changes:
#
#   * Detection is by EXTENTS, not inode.  A clone is a *distinct* inode that
#     shares the basis's physical extents, so the link-dest inode-identity check
#     would never fire; filefrag extent equality is the reflink signal.
#   * The files are 64K.  A tiny file is inlined on btrfs and cannot be cloned,
#     so a successfully *followed* basis would produce no shared extents and
#     look like a refusal -- the matrix would then pass vacuously.
#
# Needs a reflink-capable scratch filesystem (skips otherwise); the harness
# handles the uid requirements of planting foreign-owned symlinks.

import os
import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, rsync_argv, run_symlink_matrix, plant_operator_symlink,
    test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink

T = 1234567890      # shared mtime so the quick check treats the basis as a match
SIZE = 64 * 1024    # above the inline-data threshold so a follow really reflinks

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    data = os.urandom(SIZE)
    (src / 'f0').write_bytes(data)
    opt, escape = plant_operator_symlink(ctx, dest)  # rel --clone-dest anchors at dest
    escape.mkdir(parents=True, exist_ok=True)
    (escape / 'f0').write_bytes(data)                 # identical out-of-tree basis
    os.utime(src / 'f0', (T, T))
    os.utime(escape / 'f0', (T, T))
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--clone-dest={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    df = dest / 'f0'
    if not df.exists():
        return False
    try:
        # Followed == dest/f0 was reflinked from the out-of-tree basis, i.e. it
        # shares the basis's extents.  Require a non-empty extent list so an
        # unmeasurable file never counts as "followed".
        ext = filefrag_extents(df)
        return bool(ext) and ext == filefrag_extents(escape / 'f0')
    except OSError:
        return False


run_symlink_matrix('--clone-dest', case)
print("--clone-dest symlink policy matrix: enforced")
