"""WU-3: tests for remote_worker.sync (the round trip).

Rules under test:
1. ``mount_repo`` builds the exact repository source dict and rejects
   incomplete input.
2. ``apply_to_branch`` never lets the sandbox's own ``.git`` reach the local
   clone, and never leaves build artifacts behind — the exact failure seen
   in the PoC (E3), where a root-owned ``__pycache__`` entry aborted
   ``git add``.
3. A source file with mode 000 inside the tar does not abort the apply.
4. ``run_tests`` reports the project's own test result faithfully.
5. (hardening S2) Extraction is fail-closed: a member that escapes the
   destination (``../``, absolute), arrives as a symlink/hardlink/device,
   or overshoots its declared size aborts the delivery. There is no
   unfiltered ``extractall`` fallback on any supported Python.
"""

import io
import os
import subprocess
import tarfile
import tempfile
import unittest

try:
    from remote_worker.sync import apply_to_branch, mount_repo, run_tests
except ImportError:
    apply_to_branch = None

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(repo, *args):
    return subprocess.run(
        [*GIT, "-C", repo, *args], capture_output=True, text=True
    )


def make_origin(tmp):
    """A real git repo with one commit; its path doubles as a clone URL."""
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    git(origin, "init", "-q")
    with open(os.path.join(origin, "main.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a + b\n")
    git(origin, "add", "main.py")
    git(origin, "commit", "-qm", "initial")
    return origin


def add_member(path, name, *, data="", mode=0o644, typ=tarfile.REGTYPE,
               linkname="", declared_size=None):
    """Append one member to the tar at ``path``, with full control."""
    with tarfile.open(path, "a") as tar:
        info = tarfile.TarInfo(name)
        payload = data.encode()
        info.size = len(payload) if declared_size is None else declared_size
        info.mode = mode
        info.type = typ
        info.linkname = linkname
        tar.addfile(info, io.BytesIO(payload))


def make_worker_tar(tmp, *, pycache_mode=0o000):
    """A tar shaped like a downloaded sandbox workspace."""
    path = os.path.join(tmp, "workspace.tar")
    with tarfile.open(path, "w") as tar:
        def add(name, data, mode=0o644):
            info = tarfile.TarInfo(name)
            payload = data.encode()
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))

        add("worker-repo/main.py",
            "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n"
            "    return a * b\n")
        add("worker-repo/.git/HEAD", "ref: refs/heads/main\n")
        add("worker-repo/.git/index", "BINARYISH")
        add("worker-repo/__pycache__/stale.cpython-312.pyc", "x",
            mode=pycache_mode)
    return path


class TestMountRepo(unittest.TestCase):
    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")

    def test_source_dict_is_exact(self):
        source = mount_repo(
            "https://github.com/o/r", "/workspace/r"
        )
        self.assertEqual(source, {
            "type": "repository",
            "source": "https://github.com/o/r",
            "target": "/workspace/r",
        })

    def test_rejects_incomplete_input(self):
        with self.assertRaises(ValueError):
            mount_repo("", "/workspace/r")
        with self.assertRaises(ValueError):
            mount_repo("https://github.com/o/r", "relative/path")


class TestApplyToBranch(unittest.TestCase):
    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.origin = make_origin(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    # Rule 2: the worker's .git never reaches the clone, and the clone's own
    # history still works afterwards.
    def test_worker_git_is_not_overlaid(self):
        tar = make_worker_tar(self.tmp)
        result = apply_to_branch(
            tar_path=tar, repo_url=self.origin,
            branch="worker/test", workdir=os.path.join(self.tmp, "clone"),
        )
        clone = result["clone_dir"]
        head = git(clone, "log", "--oneline")
        self.assertEqual(head.returncode, 0, head.stderr)
        self.assertIn("initial", head.stdout)
        # the local .git is the clone's, not the tar's synthetic one
        with open(os.path.join(clone, ".git", "HEAD")) as fh:
            self.assertIn("ref:", fh.read())

    # Rule 2 + 3: build artifacts are purged, so apply succeeds and the diff
    # holds only the real change.
    def test_pycache_is_purged_and_diff_is_clean(self):
        tar = make_worker_tar(self.tmp)
        result = apply_to_branch(
            tar_path=tar, repo_url=self.origin,
            branch="worker/test", workdir=os.path.join(self.tmp, "clone"),
        )
        clone = result["clone_dir"]
        self.assertFalse(os.path.exists(os.path.join(clone, "__pycache__")))
        self.assertIn("multiply", result["diff"])
        self.assertNotIn("__pycache__", result["diff"])
        self.assertNotIn(".git/", result["diff"])

    def test_missing_repo_dir_in_tar_is_reported(self):
        empty = os.path.join(self.tmp, "empty.tar")
        with tarfile.open(empty, "w"):
            pass
        with self.assertRaises(ValueError):
            apply_to_branch(
                tar_path=empty, repo_url=self.origin,
                branch="worker/test",
                workdir=os.path.join(self.tmp, "clone"),
            )


class StaleCloneTest(unittest.TestCase):
    """F3: a clone left by an earlier task must never absorb a new delivery.

    T25's live run found the shape: the clone lives at a fixed per-project
    path, and `apply_to_branch` re-cloned only when the directory had no
    `.git` -- so the branch of a task for repository B landed on a clone of
    repository A (the worker-poc-toy PoC leftover), while the real
    repository never received the branch. The user would merge unrelated
    history and lose the delivery.
    """

    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        # A repo that is NOT the delivery target (the stale one).
        self.other = make_origin(self.tmp)
        # The repo the delivery belongs to.
        self.target = os.path.join(self.tmp, "target")
        os.makedirs(self.target)
        git(self.target, "init", "-q")
        with open(os.path.join(self.target, "app.py"), "w") as fh:
            fh.write("def greet():\n    return 'hi'\n")
        git(self.target, "add", "app.py")
        git(self.target, "commit", "-qm", "target initial")
        self.clone = os.path.join(self.tmp, "clone")
        subprocess.run([*GIT, "clone", "-q", self.other, self.clone],
                       capture_output=True, text=True, check=True)
        self.tar = self._delivery_tar()

    def _delivery_tar(self):
        path = os.path.join(self.tmp, "workspace.tar")
        with tarfile.open(path, "w") as tar:
            payload = b"def greet():\n    return 'hello'\n"
            info = tarfile.TarInfo("target/app.py")
            info.size = len(payload)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(payload))
        return path

    def _backups(self):
        return [n for n in os.listdir(self.tmp) if n.startswith("clone.bak-")]

    # The defect: the stale clone's origin is another repository.
    def test_a_clone_of_another_repo_never_absorbs_the_delivery(self):
        result = apply_to_branch(
            tar_path=self.tar, repo_url=self.target,
            branch="worker/f3", workdir=self.clone,
        )
        clone = result["clone_dir"]
        origin = git(clone, "remote", "get-url", "origin").stdout.strip()
        self.assertEqual(os.path.realpath(origin),
                         os.path.realpath(self.target))
        # The delivered content is in the working tree, on the target's
        # history -- and the previous clone is kept, not silently destroyed.
        with open(os.path.join(clone, "app.py")) as fh:
            self.assertIn("hello", fh.read())
        self.assertIn("hello", result["diff"])
        backups = self._backups()
        self.assertEqual(len(backups), 1, backups)
        kept = git(os.path.join(self.tmp, backups[0]), "remote",
                   "get-url", "origin").stdout.strip()
        self.assertEqual(os.path.realpath(kept), os.path.realpath(self.other))

    # The fast path stays fast: a clone of the same repo is reused.
    def test_a_clone_of_the_same_repo_is_reused(self):
        apply_to_branch(
            tar_path=self.tar, repo_url=self.other,
            branch="worker/one", workdir=self.clone,
        )
        apply_to_branch(
            tar_path=self.tar, repo_url=self.other,
            branch="worker/two", workdir=self.clone,
        )
        self.assertEqual(self._backups(), [])
        origin = git(self.clone, "remote", "get-url", "origin").stdout.strip()
        self.assertEqual(os.path.realpath(origin),
                         os.path.realpath(self.other))


class TestRunTests(unittest.TestCase):
    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")

    def test_passing_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, output = run_tests(tmp, ["python3", "-c", "print('all good')"])
        self.assertEqual(rc, 0)
        self.assertIn("all good", output)

    def test_failing_command_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, output = run_tests(
                tmp, ["python3", "-c", "import sys; sys.exit(3)"]
            )
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()

class TestApplyPurgesRootPycache(unittest.TestCase):
    """A __pycache__ at the repo root must not reach the branch diff."""

    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.origin = make_origin(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_root_pycache_is_purged_before_overlay(self):
        tar = make_worker_tar(self.tmp)  # includes __pycache__/stale.cpython-312.pyc
        result = apply_to_branch(
            tar_path=tar, repo_url=self.origin,
            branch="worker/purge", workdir=os.path.join(self.tmp, "clone"),
        )
        clone = result["clone_dir"]
        self.assertNotIn("__pycache__", result["diff"])
        self.assertFalse(os.path.exists(os.path.join(clone, "__pycache__")))


class TestRunTestsDoesNotPolluteDiff(unittest.TestCase):
    """The project's own tests must not add build artifacts to the clone,
    because the user reviews the diff after the tests run."""

    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")

    def test_run_tests_with_nobytes_adds_no_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = run_tests(
                tmp,
                ["python3", "-c",
                 "import sys, os; sys.path.insert(0, os.getcwd()); "
                 "open('m.py','w').write('x=1'); import m; print('ok')"],
            )
            self.assertEqual(rc, 0)
            self.assertFalse(
                os.path.exists(os.path.join(tmp, "__pycache__")),
                "run_tests created a __pycache__ in the clone",
            )


class UnsafeTarMemberTest(unittest.TestCase):
    """Hardening S2: extraction is fail-closed on every supported Python.

    The delivery applies a tar built inside a sandbox a prompted agent
    controlled. A member that escapes the destination (``../``, absolute
    path), arrives as a symlink/hardlink/device, or pushes the tree past
    the declared cap aborts the WHOLE delivery — never a silent skip,
    never the pre-3.12 unfiltered fallback (odd/tasks/hardening.md S2).
    """

    def setUp(self):
        if apply_to_branch is None:
            self.fail("RED: remote_worker.sync not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.origin = make_origin(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def _tar(self, name="workspace.tar"):
        """A tar with one clean member; tests append the hostile one."""
        path = os.path.join(self.tmp, name)
        with tarfile.open(path, "w"):
            pass
        add_member(path, "worker-repo/main.py",
                   data="def add(a, b):\n    return a + b\n")
        return path

    def _apply(self, tar):
        return apply_to_branch(
            tar_path=tar, repo_url=self.origin, branch="worker/test",
            workdir=os.path.join(self.tmp, "clone"),
        )

    def test_traversal_member_rejected(self):
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        add_member(tar, "worker-repo/../../evil.txt", data="pwned")
        with self.assertRaises(UnsafeTarMemberError):
            self._apply(tar)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_absolute_member_rejected(self):
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        add_member(tar, "/etc/evil.conf", data="pwned")
        with self.assertRaises(UnsafeTarMemberError):
            self._apply(tar)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "etc")))

    def test_symlink_member_rejected(self):
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        add_member(tar, "worker-repo/escape",
                   typ=tarfile.SYMTYPE, linkname="/etc/passwd")
        with self.assertRaises(UnsafeTarMemberError):
            self._apply(tar)
        self.assertFalse(
            os.path.lexists(os.path.join(self.tmp, "clone", "escape"))
        )

    def test_hardlink_member_rejected(self):
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        add_member(tar, "worker-repo/hardlink",
                   typ=tarfile.LNKTYPE, linkname="/etc/passwd")
        with self.assertRaises(UnsafeTarMemberError):
            self._apply(tar)

    def test_fifo_member_rejected(self):
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        add_member(tar, "worker-repo/pipe", typ=tarfile.FIFOTYPE)
        with self.assertRaises(UnsafeTarMemberError):
            self._apply(tar)

    def test_total_declared_size_cap_rejects(self):
        from unittest import mock
        from remote_worker import sync
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        with mock.patch.object(sync, "EXTRACT_TOTAL_LIMIT_BYTES", 10):
            with self.assertRaises(UnsafeTarMemberError):
                self._apply(tar)

    def test_python_without_filter_still_rejects(self):
        """The claim's core: no unfiltered fallback on Python < 3.12.

        Simulate the old interpreter by making extractall reject the
        ``filter`` argument exactly like an unpatched 3.10/3.11 does.
        The delivery must still refuse the hostile member — the TypeError
        branch must never fall through to a plain ``extractall(dest)``.
        """
        from unittest import mock
        from remote_worker.sync import UnsafeTarMemberError
        tar = self._tar()
        add_member(tar, "worker-repo/../../evil.txt", data="pwned")
        real_extractall = tarfile.TarFile.extractall

        def old_python(self, *args, **kwargs):
            if "filter" in kwargs:
                raise TypeError("extractall() got an unexpected keyword "
                                "argument 'filter'")
            return real_extractall(self, *args, **kwargs)

        with mock.patch.object(tarfile.TarFile, "extractall", old_python):
            with self.assertRaises(UnsafeTarMemberError):
                self._apply(tar)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_clean_tar_still_applies(self):
        result = self._apply(make_worker_tar(self.tmp))
        self.assertIn("multiply", result["diff"])
