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
from clone_dest_lib import filefrag_extents, supports_reflink, clone_dest_xattrs_check

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs)")
if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling)")
if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

clone_dest_xattrs_check(SCRATCHDIR, filefrag_extents)

print("clone-dest-xattrs: data reflinked, source xattrs applied over the clone")
