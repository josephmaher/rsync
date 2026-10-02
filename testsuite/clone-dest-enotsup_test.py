#!/usr/bin/env python3
"""--clone-dest must fall back to a local copy when FICLONE fails, whatever the
errno, and must report the entry only once.

do_clone() issues FICLONE, and the destination is free to refuse it: a
non-reflink filesystem answers EOPNOTSUPP, a cross-device basis answers EXDEV,
an inlined or misaligned extent EINVAL.  The generator then goes to try_a_copy
and copies the basis locally (copy_altdest_file), so the file still lands
correctly without being re-sent over the wire.  An earlier version of the patch
called exit_cleanup(RERR_UNSUPPORTED) on clone failure and aborted the whole
transfer; this test is the regression lock for the fallback.

Forcing the errno with an LD_PRELOAD hook on ioctl() reaches the fallback arm
deterministically -- on a filesystem that could reflink perfectly well, and on
one that can't.  So unlike the other clone-dest tests this needs NO reflink
support: it runs anywhere with Linux and a C compiler, which makes it the one
clone-dest test that exercises real clone-dest code in non-reflink CI.

Modelled on link-dest-symlink-enotsup_test.py.
"""

import os
import platform
import re
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped,
)

if platform.system() != 'Linux':
    test_skipped('the LD_PRELOAD ioctl hook is Linux-only')

hook_code = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <linux/fs.h>     /* FICLONE, arch-correct */
#include <unistd.h>

static int (*real_ioctl)(int, unsigned long, ...);

static void mark(const char *var)
{
    const char *path = getenv(var);
    int fd;
    if (!path)
        return;
    fd = open(path, O_WRONLY | O_CREAT, 0600);
    if (fd >= 0)
        close(fd);
}

static int refuse_errno(void)
{
    const char *e = getenv("RSYNC_CLONE_REFUSE_ERRNO");
    if (e && strcmp(e, "EXDEV") == 0)
        return EXDEV;
    if (e && strcmp(e, "EINVAL") == 0)
        return EINVAL;
    if (e && strcmp(e, "ENOTTY") == 0)
        return ENOTTY;
    return EOPNOTSUPP;
}

/* Only FICLONE is refused; every other ioctl rsync makes passes through, so
 * nothing else in the transfer is disturbed. */
int ioctl(int fd, unsigned long request, ...)
{
    va_list ap;
    void *arg;
    va_start(ap, request);
    arg = va_arg(ap, void *);
    va_end(ap);
    if (!real_ioctl)
        real_ioctl = dlsym(RTLD_NEXT, "ioctl");
    if (request == FICLONE) {
        mark("RSYNC_CLONE_HOOK_MARKER");
        errno = refuse_errno();
        return -1;
    }
    return real_ioctl(fd, request, arg);
}
'''

base = SCRATCHDIR / 'clone-dest-enotsup'
rmtree(base)
src = base / 'src'
cdest = base / 'cdest'      # the --clone-dest basis
makepath(src, cdest)

DATA_SIZE = 64 * 1024
data = os.urandom(DATA_SIZE)
# Identical basis with identical mtime so the quick-check matches and the
# clone is attempted: CLONE_DEST forces the match once size+mtime agree, so
# do_clone() is reached and FICLONE issued (then refused by the hook).
T = 1234567890
for d in (src, cdest):
    (d / 'f0').write_bytes(data)
    os.utime(d / 'f0', (T, T))

hook_src = base / 'hook.c'
hook_lib = base / 'hook.so'
hook_src.write_text(hook_code)
build = subprocess.run(
    ['cc', '-shared', '-fPIC', '-o', str(hook_lib), str(hook_src), '-ldl'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode != 0:
    test_skipped(f'cannot build the LD_PRELOAD ioctl hook: {build.stdout!r}')


def run(dest, refuse, args=('-a',)):
    marker = base / f'fired-{refuse}-{dest.name}'
    env = os.environ.copy()
    env.update({
        'LD_PRELOAD': str(hook_lib),
        'RSYNC_CLONE_REFUSE_ERRNO': refuse,
        'RSYNC_CLONE_HOOK_MARKER': str(marker),
    })
    r = subprocess.run(
        rsync_argv(*args, '--stats', f'--clone-dest={cdest}', f'{src}/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if r.returncode is not None and r.returncode < 0:
        test_fail(f'rsync died by signal {-r.returncode} under the hook: '
                  f'output={r.stdout!r}')
    if not marker.exists():
        # do_clone() never issued FICLONE: the basis didn't match, or this
        # build has no FICLONE.  Not a fallback failure -- the arm is unreached.
        test_skipped(f'the LD_PRELOAD hook never fired, so do_clone() never '
                     f'reached FICLONE (rc={r.returncode}, output={r.stdout!r})')
    return r


def literal_bytes(out):
    m = re.search(r'Literal data:\s*([\d,]+)', out)
    return int(m.group(1).replace(',', '')) if m else -1


def check_fallback(dest, refuse):
    got = run(dest, refuse)
    ctx = f'refuse={refuse}, rc={got.returncode}, output={got.stdout!r}'
    if got.returncode != 0:
        test_fail('a refused FICLONE failed the transfer instead of falling '
                  f'back to a copy of the basis: {ctx}')
    df = dest / 'f0'
    if not df.is_file() or df.read_bytes() != data:
        test_fail(f'the fallback did not produce the right file content: {ctx}')
    # The fallback copies the basis LOCALLY (copy_altdest_file), so the data
    # must not have been re-sent over the wire.  This separates "fell back to
    # the basis" from "gave up on --clone-dest and re-transferred".
    lit = literal_bytes(got.stdout)
    if lit > DATA_SIZE // 2:
        test_fail(f'the fallback re-sent the data over the wire '
                  f'(Literal data={lit}) instead of copying the basis: {ctx}')


# EOPNOTSUPP is what a non-reflink destination answers.
check_fallback(base / 'dest-eopnotsupp', 'EOPNOTSUPP')
# EXDEV is a cross-device basis; EINVAL an inlined/misaligned extent.  The
# generator's try_a_copy fallback is errno-agnostic, so these reach the same
# place -- the file is copied correctly either way.
check_fallback(base / 'dest-exdev', 'EXDEV')
check_fallback(base / 'dest-einval', 'EINVAL')

# The fallback must report the entry ONCE.  do_clone()'s own "cf" itemize is
# skipped when it fails (goto try_a_copy jumps past it) and try_a_copy itemizes
# the local copy itself -- a fallback that itemized on both paths would print
# the file twice, which a plain -a run cannot see.
dest = base / 'dest-itemize'
got = run(dest, 'EOPNOTSUPP', args=('-ivvplrt',))
ctx = f'rc={got.returncode}, output={got.stdout!r}'
if got.returncode != 0:
    test_fail(f'the itemised run did not succeed, so its output proves nothing: {ctx}')
# Anchored on the change-type letters as well as the name, so a stray warning
# line mentioning f0 cannot satisfy the count.
f0_lines = [ln for ln in got.stdout.splitlines()
            if re.match(r'^cf\S*\s+f0$', ln)]
if len(f0_lines) != 1:
    test_fail(f'expected exactly one "cf... f0" itemisation for the fallback '
              f'copy, got {len(f0_lines)}: {f0_lines} ({ctx})')
