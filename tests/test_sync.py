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
