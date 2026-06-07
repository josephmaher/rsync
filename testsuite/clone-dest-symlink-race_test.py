#!/usr/bin/env python3
# Clone-dest analogue of alt-dest-symlink-race_test.py (which covers
# --link-dest / --copy-dest / --compare-dest).
#
# Targets the *basedir-confinement* path specifically: a parent symlink ON the
# alt-dest basedir. In a use-chroot=no daemon a local attacker who can write
# into a module plants module/cd -> /outside and then pushes with
# --clone-dest=cd. If do_clone's basedir resolution followed that symlink, the
# receiver would reflink /outside/target.txt into the client-readable module
# (read disclosure). do_clone resolves the basedir through
# secure_relative_open(), which RESOLVE_BENEATH rejects, so the outside content
# must never appear in the module.
#
# This is a SYSTEM-level basedir-escape test. Like the daemon attack in
# clone_dest_lib, a *static* planted symlink may be caught by the basis lookup
# (link_stat in try_dests_reg) before do_clone is reached; either layer
# refusing is a correct outcome. The unit-level isolation of do_clone's
# confinement lives in clone-dest-secure_test.py / t_clone.
#
# Detection is by content/extents, not inode: a clone produces a *different*
# inode but *shared* extents, so the link-dest inode check is replaced by a
# filefrag extent comparison (and a marker-content check).
#
# Needs a reflink-capable scratch filesystem (FICLONE); skips otherwise.

import os
import platform
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rsync_argv, get_testuid, get_rootuid, get_rootgid,
    rmtree, start_test_daemon, test_fail, test_skipped,
)
from clone_dest_lib import filefrag_extents, supports_reflink

DAEMON_PORT = 12888  # distinct from the other daemon tests

if platform.system() != 'Linux':
    test_skipped("--clone-dest reflinks require Linux (FICLONE)")
if not shutil.which('/sbin/filefrag'):
    test_skipped("can't find filefrag (e2fsprogs), needed to verify reflinks")

mod = SCRATCHDIR / 'module'
outside = SCRATCHDIR / 'outside'
src_dir = SCRATCHDIR / 'src_files'
conf = SCRATCHDIR / 'test-rsyncd.conf'

for d in (mod, outside, src_dir):
    rmtree(d)
    d.mkdir(parents=True)

if not supports_reflink(SCRATCHDIR):
    test_skipped(f"filesystem under {SCRATCHDIR} does not support reflinks")

# The outside file the attacker wants the daemon to clone in. 64K so it exceeds
# the inline-data threshold: an inlined file can't be reflinked, which would
# make a successful escape produce no shared extents and the test vacuous. A
# distinctive marker lets us detect the content landing in the module.
SIZE = 64 * 1024
marker = b"OUTSIDE_SECRET_DATA_MUST_NOT_CLONE_IN\n"
secret = marker + b"S" * (SIZE - len(marker))
(outside / 'target.txt').write_bytes(secret)
os.chmod(outside / 'target.txt', 0o644)

# Attacker-planted basedir symlink inside the module.
os.symlink(str(outside), mod / 'cd')

# Source: clone-dest only reflinks when the quick-check (size+mtime) passes and
# THEN forces a match for CLONE_DEST, so the pushed file must match the basis
# in size and mtime or clone-dest never attempts the (escaped) basis and the
# test can't tell escape from non-escape. Same size, same mtime/mode as the
# outside file -- but DIFFERENT bytes, so a normal (confined) transfer leaves
# the client's content, never the secret.
(src_dir / 'target.txt').write_bytes(b"P" * SIZE)
ref = (outside / 'target.txt').stat()
os.utime(src_dir / 'target.txt', (ref.st_atime, ref.st_mtime))
os.chmod(src_dir / 'target.txt', 0o644)

my_uid = get_testuid()
root_uid = get_rootuid()
root_gid = get_rootgid()
uid_line = f"uid = {root_uid}"
gid_line = f"gid = {root_gid}"
if my_uid != root_uid:
    uid_line = '#' + uid_line
    gid_line = '#' + gid_line

conf.write_text(f"""\
use chroot = no
{uid_line}
{gid_line}
log file = {SCRATCHDIR}/rsyncd.log
[upload]
    path = {mod}
    use chroot = no
    read only = no
""")

url = start_test_daemon(conf, DAEMON_PORT)

# Push directly into the module root: pushing into a destination subdir would
# make the receiver chdir into it before resolving --clone-dest, so "cd" would
# resolve in the wrong CWD and mask the bug. -t preserves mtime (needed for the
# quick-check above), -p preserves mode.
subprocess.run(
    rsync_argv('-rtp', '--clone-dest=cd', f'{src_dir}/', f'{url}upload/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

target = mod / 'target.txt'
# We don't require the push to succeed (a refused clone may fall back to a
# normal transfer or, depending on the generator, abort); we require only that
# the outside content never landed in the module.
if target.is_file():
    data = target.read_bytes()
    if marker in data:
        test_fail(
            "basedir-escape: outside/target.txt content was cloned into "
            "module/target.txt -- do_clone followed the parent symlink on the "
            "basedir (--clone-dest=cd, cd -> outside)"
        )
    if filefrag_extents(target) == filefrag_extents(outside / 'target.txt'):
        test_fail(
            "basedir-escape: module/target.txt shares extents with "
            "outside/target.txt -- the basedir symlink was followed and the "
            "outside file reflinked in"
        )

# The outside file must be untouched (no write-escape the other way).
if (outside / 'target.txt').read_bytes() != secret:
    test_fail("outside/target.txt was modified -- write escaped the module")
