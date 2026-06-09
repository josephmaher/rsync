#!/usr/bin/env python3
# --clone-dest composed with -H (hard-link preservation).
#
# Clone analogue of the "-aH --link-dest" line in hardlinks_test.py. -H and
# --clone-dest pull on different primitives -- -H wants several names to share
# one inode, a reflink gives a *distinct* inode -- so their composition is a
# real seam worth pinning. The good outcome (confirmed on btrfs): a hard-linked
# source group becomes ONE inode on the destination (so -H is preserved) whose
# extents are SHARED with the basis (so it was reflinked, not freshly copied).
#
# How it composes in try_dests_reg: the group's first member (by name) gets the
# clone-dest basis lookup and is reflinked; the remaining members are
# hard-linked to that first dest file by finish_hard_link. So the group reflinks
# iff the FIRST-named member has a same-named basis in the clone-dest (alt-dest
# matches by path, not content) -- which is why the basis here is a full copy.
#
# Needs a reflink-capable scratch filesystem (FICLONE); skips otherwise.

import os
import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, rmtree, run_rsync, test_fail, test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")

work = SCRATCHDIR / 'hlink'
rmtree(work)
work.mkdir(parents=True)

if not supports_reflink(work):
    test_skipped(f"filesystem under {work} does not support reflinks")

d1, d2, d3 = work / '1', work / '2', work / '3'
d1.mkdir()

# A hard-linked group (name1/name2/name3 == one inode) plus an independent
# file (name4) with identical content. 64K so the files are reflinkable
# (inlined data can't be cloned).
data = os.urandom(64 * 1024)
(d1 / 'name1').write_bytes(data)
os.link(d1 / 'name1', d1 / 'name2')
os.link(d1 / 'name1', d1 / 'name3')
(d1 / 'name4').write_bytes(data)   # same bytes, separate inode (not linked)

# Basis: a plain (-a, no -H) copy, so 2/ holds four independent same-content
# files. clone-dest will reflink each transferred file from its same-named
# basis here.
run_rsync('-a', f'{d1}/', f'{d2}/')

# The transfer under test: -H must rebuild the hard-link group in 3/, and
# clone-dest must reflink it from 2/.
proc = subprocess.run(
    rsync_argv('-aH', f'--clone-dest={d2}', f'{d1}/', f'{d3}/'),
    capture_output=True, text=True,
)
if proc.returncode != 0:
    test_fail(f"rsync -aH --clone-dest failed (rc={proc.returncode})\n"
              f"{proc.stdout}{proc.stderr}")

ino = lambda p: os.stat(p).st_ino

# 1) -H preserved: name1/name2/name3 in 3/ are a single shared inode.
i1, i2, i3 = ino(d3 / 'name1'), ino(d3 / 'name2'), ino(d3 / 'name3')
if not (i1 == i2 == i3):
    test_fail(f"-H not preserved: 3/name1..name3 are not one inode "
              f"(inodes {i1}, {i2}, {i3})")

# 2) The surviving group inode is a REFLINK of the basis, not a fresh copy:
#    it shares 2/name1's extents (name1 is the first-named group member, so it
#    is the one whose basis lookup drives the clone).
ext_group = filefrag_extents(d3 / 'name1')
ext_basis = filefrag_extents(d2 / 'name1')
if not ext_group:
    test_fail("no extents found for 3/name1 (inlined, or filefrag not parsed?)")
if ext_group != ext_basis:
    test_fail("clone-dest did not reflink the hard-link group: 3/name1 does "
              "not share extents with the basis 2/name1 (was it copied fresh?)")

# 3) Group boundary respected: name4 (not hard-linked in the source) is a
#    DISTINCT inode from the group, and is itself reflinked from 2/name4.
i4 = ino(d3 / 'name4')
if i4 == i1:
    test_fail(f"group boundary violated: 3/name4 shares the group's inode "
              f"({i4}) though it was not hard-linked in the source")
ext_n4 = filefrag_extents(d3 / 'name4')
if not ext_n4:
    test_fail("no extents found for 3/name4")
if ext_n4 != filefrag_extents(d2 / 'name4'):
    test_fail("3/name4 does not share extents with its basis 2/name4 "
              "(independent file was not reflinked)")

print("clone-dest-hardlinks: -H group preserved and reflinked from the basis")
