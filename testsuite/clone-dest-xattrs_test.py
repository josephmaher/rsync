#!/usr/bin/env python3
# --clone-dest composed with -X (extended attributes).
#
# A reflink (FICLONE) copies DATA EXTENTS only -- xattrs live in inode
# metadata and are NOT carried by the clone. So after do_clone() reflinks a
# file from the basis, the destination's xattrs come from finish_transfer() /
# set_file_attrs() applying the SOURCE file's metadata, not from the basis and
# not from the freshly-created (empty-xattr) clone inode.
#
# This also exercises a clone-dest-specific subtlety: CLONE_DEST forces a match
# once the quick-check (size+mtime) passes, skipping unchanged_attrs() -- so the
# clone fires even when the basis's xattrs DIFFER from the source's. The test
# makes them differ on purpose, so "target got the source's xattr" actually
# proves provenance (rather than passing vacuously because all three agree).
#
# Asserts: (1) the data was reflinked (target shares the basis's extents),
# (2) the target carries the SOURCE's xattr, not the basis's.
#
# Needs a reflink-capable scratch filesystem (FICLONE) and xattr support; skips
# otherwise. Runs at any uid.

import os
import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, rmtree, test_fail, test_skipped,
    xattr_set, xattr_dump, xattrs_supported,
)
from clone_dest_lib import filefrag_extents, supports_reflink

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")
if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling)")

work = SCRATCHDIR / 'xattrs'
rmtree(work)
work.mkdir(parents=True)

if not supports_reflink(work):
    test_skipped(f"filesystem under {work} does not support reflinks")

src = work / 'source'
ref = work / 'clone'      # the --clone-dest basis
dst = work / 'target'
src.mkdir()
ref.mkdir()

# Identical 64K data in source and basis so the size+mtime quick-check matches
# and the clone fires; 64K clears the inline threshold so it's reflinkable.
data = os.urandom(64 * 1024)
(src / 'a').write_bytes(data)
(ref / 'a').write_bytes(data)

# Same mtime (quick-check requires it) ...
st = (src / 'a').stat()
os.utime(ref / 'a', (st.st_atime, st.st_mtime))

# ... but DIFFERENT xattrs, so the provenance check below is meaningful: if the
# target ends up with "fromsrc" the source won; "frombasis" would mean the
# basis's metadata leaked through the clone.
try:
    xattr_set('user.comment', 'fromsrc', src / 'a')
except OSError:
    test_skipped("unable to set an xattr on the scratch filesystem")
xattr_set('user.comment', 'frombasis', ref / 'a')

proc = subprocess.run(
    rsync_argv('-aX', f'--clone-dest={ref}', f'{src}/', f'{dst}/'),
    capture_output=True, text=True,
)
if proc.returncode != 0:
    test_fail(f"rsync -aX --clone-dest failed (rc={proc.returncode})\n"
              f"{proc.stdout}{proc.stderr}")

target = dst / 'a'
if not target.is_file():
    test_fail(f"destination file missing ({target})")

# 1) Data was reflinked: target shares the basis's physical extents.
if filefrag_extents(target) != filefrag_extents(ref / 'a'):
    test_fail("clone-dest did not reflink with -X: target/a does not share "
              "extents with the basis clone/a")

# 2) Xattrs came from the SOURCE, not the basis (and weren't dropped). The
#    clone carries no xattrs of its own, so this proves finish_transfer applied
#    the source's metadata over the reflinked data. xattr_dump emits sorted
#    name="value" lines (a file with no user xattrs is omitted entirely).
dump = xattr_dump(target)
if 'user.comment="fromsrc"' not in dump:
    test_fail("clone-dest -X: target/a does not carry the source's xattr "
              f"user.comment=\"fromsrc\" (xattrs dropped or wrong value):\n{dump!r}")
if 'user.comment="frombasis"' in dump:
    test_fail("clone-dest -X: target/a carries the BASIS's xattr "
              f"user.comment=\"frombasis\" -- basis metadata leaked through "
              f"the clone:\n{dump!r}")

print("clone-dest-xattrs: data reflinked, source xattrs applied over the clone")
