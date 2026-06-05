#!/usr/bin/env python3
"""--clone-dest at depth and across directory boundaries.

Companion to alt-dest-deep_test.py (which covers --link-dest / --copy-dest /
--compare-dest). Asserts the distinguishing property of --clone-dest at every
level of a >=3-deep tree, with the reference tree placed OUTSIDE both the
source and destination trees (a sibling, not a parent/child):

  unchanged files are REFLINKED from the reference -- a *different* inode but
                 SHARED physical extents (not a hard link);
  a size-changed file is transferred fresh -- INDEPENDENT extents.

This drives the two-dirfd / outside-tree path resolution that do_clone's
confinement rewrites, at depth. Unlike alt-dest-deep_test.py it needs a
reflink-capable scratch filesystem (skips otherwise) and filefrag to verify
sharing, but it still runs unprivileged.

Note the asymmetry with --link-dest: clone-dest only reflinks when the
quick-check (size+mtime) passes, so the changed file below must differ in
SIZE to fall through to a normal transfer. A same-size, same-mtime change
would be cloned from the now-stale basis -- a known property of clone-dest,
not exercised here.
"""

import os
import platform
import shutil

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_exists, assert_same, make_tree, rmtree, run_rsync, walk_files,
    test_fail, test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")

src = FROMDIR
ref = SCRATCHDIR / 'altref'   # sibling of from/ and to/ -- outside both trees

rmtree(src)
rmtree(ref)
rmtree(TODIR)

# from/, to/ and altref/ are all under the scratch tree, i.e. one filesystem;
# probing it here represents the ref->to filesystem the clone reflinks across.
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

# A >=3-deep source: f0, d1/f1, d1/d2/f2, d1/d2/d3/f3. data_size=64K clears any
# inline-data threshold (inlined files can't be reflinked), matching the other
# clone-dest tests.
make_tree(src, depth=3, data=True, data_size=64 * 1024)

# Reference == an exact copy of the source, so every file is a clone-dest match
# (same size and mtime, which is what clone-dest's quick-check requires).
run_rsync('-a', f'{src}/', f'{ref}/')

# Change the deepest file's SIZE so it must be transferred fresh (see the
# module docstring on why a size change, specifically, is required).
changed = os.path.join('d1', 'd2', 'd3', 'f3')
with open(src / changed, 'ab') as f:
    f.write(b'a changed deep tail\n')

rels = [p.relative_to(src) for p in walk_files(src)]
assert changed in [str(r) for r in rels]

run_rsync('-a', f'--clone-dest={ref}', f'{src}/', f'{TODIR}/')
os.sync()  # flush before measuring extents (matters on bcachefs/xfs)

for rel in rels:
    d, r = TODIR / rel, ref / rel
    # The transfer must be correct at every level, cloned or not.
    assert_exists(d, label=f'clone-dest {rel}')
    assert_same(d, src / rel, label=f'clone-dest {rel}')

    ext_d = filefrag_extents(d)
    ext_r = filefrag_extents(r)
    if not ext_d:
        test_fail(f"clone-dest {rel}: no extents found (inlined, or "
                  f"filefrag output not parsed?)")
    if str(rel) == changed:
        # size-changed -> normal transfer -> must NOT share the ref's extents
        if ext_d == ext_r:
            test_fail(f"clone-dest changed {rel}: unexpectedly shares extents "
                      f"with the reference (should have been transferred fresh)")
    else:
        # unchanged -> reflinked from ref -> must share the ref's extents
        if ext_d != ext_r:
            test_fail(f"clone-dest unchanged {rel}: does not share extents with "
                      f"the reference (was not reflinked)")

print("clone-dest-deep: reflink sharing verified at depth")
