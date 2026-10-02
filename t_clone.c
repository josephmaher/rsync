/* t_clone.c -- unit test for do_clone()'s symlink-race-safe confinement.
 *
 * Companion to t_secure_relpath.c. Where that exercises secure_relative_open()
 * directly, this drives the real do_clone() (the --clone-dest reflink path) in
 * its secure branch and confirms it refuses a basis whose parent component is
 * a symlink escaping the module, while still cloning a legitimate in-module
 * basis. It fails on the old path-based do_clone() and passes on the confined
 * one -- the discrimination the clone-dest integration tests cannot make
 * (they only ever reach do_clone() with legitimate within-tree paths).
 *
 * Needs a reflink-capable filesystem (do_clone issues FICLONE for the positive
 * case); the Python wrapper skips when the scratch fs can't reflink.
 *
 * IMPORTANT: the #include line and the stub-global block below must match
 * t_secure_relpath.c verbatim, since this links the SAME objects (syscall.o,
 * util1.o, util2.o, t_stub.o, lib/...). The ONE required change from that file
 * is am_daemon = 1 -- do_clone() only confines in a no-chroot daemon. If you
 * hit undefined-reference or multiple-definition errors at link time,
 * reconcile this block against t_secure_relpath.c (copy its head, set
 * am_daemon = 1, keep this main()).
 */

#include "rsync.h"

#include <sys/stat.h>

int dry_run = 0;
int am_root = 0;
int am_sender = 0;
int read_only = 0;
int list_only = 0;
int copy_links = 0;
int copy_unsafe_links = 0;
extern int am_daemon, am_chrooted;

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

static const char SECRET[] = "OUTSIDE_SECRET_MUST_NOT_BE_CLONED\n";
static const char BASIS[]  = "LEGIT_IN_MODULE_BASIS_CONTENT\n";

static int create_file(const char *path, const char *data)
{
	int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	ssize_t n;
	if (fd < 0)
		return -1;
	n = write(fd, data, strlen(data));
	close(fd);
	return n == (ssize_t)strlen(data) ? 0 : -1;
}

/* 1 if <path> exists and its contents equal <data>, else 0. */
static int file_has(const char *path, const char *data)
{
	char buf[512];
	ssize_t n;
	int fd = open(path, O_RDONLY);
	if (fd < 0)
		return 0;
	n = read(fd, buf, sizeof buf - 1);
	close(fd);
	if (n < 0)
		return 0;
	buf[n] = '\0';
	return strcmp(buf, data) == 0;
}

int main(int argc, char *argv[])
{
	am_daemon = 1;   /* REQUIRED: do_clone() confines only in a daemon ... */
	am_chrooted = 0; /* ... that is not chrooted */

	char path[MAXPATHLEN], target[MAXPATHLEN];
	const char *testdir;
	int failures = 0, ret;

	if (argc != 2) {
		fprintf(stderr, "usage: %s TESTDIR\n", argv[0]);
		return 2;
	}
	testdir = argv[1];

	/* Layout:
	 *   TESTDIR/outside/secret          target of the escape (must stay unread)
	 *   TESTDIR/module/                 the daemon module == our CWD anchor
	 *   TESTDIR/module/realdir/basis    a legitimate in-module basis
	 *   TESTDIR/module/cd_abs -> TESTDIR/outside   (absolute-target escape)
	 *   TESTDIR/module/cd_rel -> ../outside        (relative-target escape)
	 */
	snprintf(path, sizeof path, "%s/outside", testdir);
	mkdir(path, 0700);
	snprintf(path, sizeof path, "%s/outside/secret", testdir);
	if (create_file(path, SECRET) < 0) { perror("write secret"); return 2; }

	snprintf(path, sizeof path, "%s/module", testdir);
	mkdir(path, 0700);
	snprintf(path, sizeof path, "%s/module/realdir", testdir);
	mkdir(path, 0700);
	snprintf(path, sizeof path, "%s/module/realdir/basis", testdir);
	if (create_file(path, BASIS) < 0) { perror("write basis"); return 2; }

	snprintf(target, sizeof target, "%s/outside", testdir);  /* absolute */
	snprintf(path, sizeof path, "%s/module/cd_abs", testdir);
	if (symlink(target, path) < 0) { perror("symlink cd_abs"); return 2; }
	snprintf(path, sizeof path, "%s/module/cd_rel", testdir);
	if (symlink("../outside", path) < 0) { perror("symlink cd_rel"); return 2; }

	/* do_clone's secure branch confines beneath CWD: become the module. */
	snprintf(path, sizeof path, "%s/module", testdir);
	if (chdir(path) < 0) { perror("chdir module"); return 2; }

	/* 1) Positive: a legitimate in-module basis must clone. Proves the test
	 *    isn't vacuously refusing everything (and that clones work here). */
	errno = 0;
	ret = do_clone("realdir/basis", "out_ok", 0600);
	if (ret != 0 || !file_has("out_ok", BASIS)) {
		fprintf(stderr, "FAIL positive: legit in-module basis did not clone "
			"(ret=%d errno=%d); is the scratch fs reflink-capable?\n",
			ret, errno);
		failures++;
	}

	/* 2) Attack via absolute-target parent symlink: do_clone must NOT clone
	 *    the outside file in. Confined -> -1 before any read; unconfined ->
	 *    clones outside/secret into out_abs (the leak this test catches). */
	errno = 0;
	ret = do_clone("cd_abs/secret", "out_abs", 0600);
	if (ret == 0 || file_has("out_abs", SECRET)) {
		fprintf(stderr, "FAIL attack(abs): do_clone followed cd_abs -> outside "
			"(ret=%d leaked=%d)\n", ret, file_has("out_abs", SECRET));
		failures++;
	}

	/* 3) Attack via relative-target parent symlink (../outside): same. */
	errno = 0;
	ret = do_clone("cd_rel/secret", "out_rel", 0600);
	if (ret == 0 || file_has("out_rel", SECRET)) {
		fprintf(stderr, "FAIL attack(rel): do_clone followed cd_rel -> ../outside "
			"(ret=%d leaked=%d)\n", ret, file_has("out_rel", SECRET));
		failures++;
	}

	if (failures) {
		fprintf(stderr, "t_clone: %d case(s) failed\n", failures);
		return 1;
	}
	return 0;
}
