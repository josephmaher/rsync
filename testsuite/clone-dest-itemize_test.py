#!/usr/bin/env python3
# Itemized-output (-i / -ii) check for --clone-dest.
#
# Clone analogue of the --copy-dest / --link-dest variant blocks in
# itemize_test.py. The point is the per-file change code: a cloned file is a
# NEW inode whose data is shared with the basis, so it must itemize like
# --copy-dest ("cf" -- created file), NOT like --link-dest ("hf" -- hard
# link). An earlier version of the patch reused link-dest's itemize call and
# mislabeled reflinks as "hf"; this test locks the corrected "cf".
#
# It also pins the -H composition: when a hard-linked group is cloned, the
# first member is the clone ("cf") and the remaining members are hard-linked
# to it ("hf => firstmember") -- the same as link-dest for the followers,
# since hard-linking subsequent group members is identical either way.
#
# Needs a reflink-capable scratch filesystem (FICLONE); skips otherwise.

import os
import platform
import shutil

from rsyncfns import (
    SCRATCHDIR,
    all_plus, allspace, dots,
    checkdiff, rmtree, run_rsync, test_skipped,
)
from clone_dest_lib import supports_reflink

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")

work = SCRATCHDIR / 'itemize'
rmtree(work)
work.mkdir(parents=True)

if not supports_reflink(work):
    test_skipped(f"filesystem under {work} does not support reflinks")

d1, d2, d3 = work / '1', work / '2', work / '3'
d1.mkdir()

# A hard-linked group (name1/name2/name3 == one inode) plus an independent
# file (name4). 64K so they are reflinkable.
data = os.urandom(64 * 1024)
(d1 / 'name1').write_bytes(data)
os.link(d1 / 'name1', d1 / 'name2')
os.link(d1 / 'name1', d1 / 'name3')
(d1 / 'name4').write_bytes(os.urandom(64 * 1024))

# Basis: a plain (-a, no -H) copy, so 2/ holds independent same-named files.
run_rsync('-a', f'{d1}/', f'{d2}/')

# Settle directory mtimes so the dir line is stable and not re-itemized for
# time on the measured run (mirrors itemize_test.py's "-f '-! */'" touch).
#run_rsync('-a', '-f', '-! */', f'{d1}/', str(d3))

# The measured transfer. Expected, line by line:
#   .d           ./                  dir, time-only (settled above)
#   cf<allspace> name1              first group member: CLONED (new inode) -> cf
#   hf<all_plus> name2 => name1     hard-linked to name1 (new link) -> hf, +++,
#   hf<all_plus> name3 => name1       with the "=> name1" target suffix
#   cf<allspace> name4              independent file: CLONED -> cf
d3.mkdir()   # pre-create the dir so only its contents itemize as new
checkdiff(['-iiplrtH', f'--clone-dest={d2}', f'{d1}/', f'{d3}/'],
          f".d{allspace} ./\n"           # or "cd{all_plus} ./" if you don't pre-create d3
          f"cf{allspace} name1\n"
          f"hf{allspace} name2 => name1\n"
          f"hf{allspace} name3 => name1\n"
          f"cf{allspace} name4\n")
