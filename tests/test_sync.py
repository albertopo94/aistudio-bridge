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


class DeliveryFreshnessTest(unittest.TestCase):
    """Hardening S4 + S9: every delivery is a clean, committed unit.

    The old apply staged onto whatever the previous task left in the
    persistent clone and never committed, so task N+1's diff --cached
    reported task N's work (and remote deletions never reached the branch
    at all). A delivery now: starts from the remote's main line, removes
    tracked files the workspace no longer holds, stages, and commits.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.origin = make_origin(self.tmp)
        with open(os.path.join(self.origin, "extra.py"), "w") as fh:
            fh.write("SECRET = True\n")
        git(self.origin, "add", "extra.py")
        git(self.origin, "commit", "-qm", "add extra")

    def tearDown(self):
        self._tmp.cleanup()

    def _tar(self, files, name="ws.tar"):
        path = os.path.join(self.tmp, name)
        with tarfile.open(path, "w"):
            pass
        for rel, data in files.items():
            add_member(path, f"worker-repo/{rel}", data=data)
        return path

    def _apply(self, files, branch, name="ws.tar"):
        return apply_to_branch(
            tar_path=self._tar(files, name), repo_url=self.origin,
            branch=branch, workdir=os.path.join(self.tmp, "clone"),
            repo_dir="worker-repo",
        )

    MAIN = "def add(a, b):\n    return a + b\n"

    def test_deletion_reaches_the_local_branch(self):
        result = self._apply({"main.py": self.MAIN}, "worker/r1")
        self.assertFalse(
            os.path.exists(os.path.join(result["clone_dir"], "extra.py")),
            "a file the workspace deleted is still in the clone",
        )
        self.assertIn("-SECRET = True", result["diff"])
        self.assertTrue(
            any("extra.py" in line and line.strip().startswith("D")
                for line in result["changed"]),
            f"no deletion in changed: {result['changed']}",
        )

    def test_delivery_is_committed_and_index_clean(self):
        result = self._apply(
            {"main.py": self.MAIN + "\nEXTRA = 1\n", "extra.py":
             "SECRET = True\n"}, "worker/r1",
        )
        clone = result["clone_dir"]
        self.assertEqual(git(clone, "status", "--short").stdout, "")
        log = git(clone, "log", "--oneline")
        self.assertEqual(len(log.stdout.splitlines()), 3, log.stdout)
        self.assertIn("worker/r1", log.stdout)

    def test_second_delivery_never_stacks_on_the_first(self):
        self._apply({"main.py": self.MAIN}, "worker/r1")
        result2 = self._apply({"main.py": self.MAIN + "\n\ndef sub(a, b):\n"
                               "    return a - b\n", "extra.py":
                               "SECRET = True\n"}, "worker/r2",
                              name="ws2.tar")
        clone = result2["clone_dir"]
        self.assertNotIn("SECRET", result2["diff"])
        self.assertFalse(
            any("extra.py" in line for line in result2["changed"]),
            f"task 1's deletion leaked into task 2: {result2['changed']}",
        )
        count = git(clone, "rev-list", "--count", "worker/r2")
        self.assertEqual(count.stdout.strip(), "3")
        count1 = git(clone, "rev-list", "--count", "worker/r1")
        self.assertEqual(count1.stdout.strip(), "3")

    def test_local_untracked_file_survives_next_delivery(self):
        first = self._apply({"main.py": self.MAIN}, "worker/r1")
        notes = os.path.join(first["clone_dir"], "notes.txt")
        with open(notes, "w") as fh:
            fh.write("mine\n")
        result2 = self._apply({"main.py": self.MAIN, "extra.py":
                               "SECRET = True\n"}, "worker/r2",
                              name="ws2.tar")
        self.assertTrue(os.path.exists(notes))
        self.assertFalse(
            any("notes.txt" in line for line in result2["changed"])
        )

    def test_identical_delivery_makes_no_empty_commit(self):
        self._apply({"main.py": self.MAIN, "extra.py": "SECRET = True\n"},
                    "worker/r1")
        clone = os.path.join(self.tmp, "clone")
        count = git(clone, "rev-list", "--count", "worker/r1")
        self.assertEqual(count.stdout.strip(), "2")


class SnapshotBaseTest(unittest.TestCase):
    """P0-1: the delivery is built against the exact snapshot the task sent,
    never against the remote line that may have advanced meanwhile.

    The old apply reset the branch to the CURRENT remote main line, so a
    branch that moved from A to B during the task absorbed a delivery that
    was built over A -- and `_drop_removed` then deleted B's files as if the
    workspace had dropped them. Passing the recorded ``base_commit`` anchors
    the delivery on the point Antigravity actually received.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.origin = make_origin(self.tmp)  # commit A
        self.base_a = git(self.origin, "rev-parse", "HEAD").stdout.strip()
        # The remote advances to B while the task runs.
        with open(os.path.join(self.origin, "b_only.py"), "w") as fh:
            fh.write("ADDED = True\n")
        git(self.origin, "add", "b_only.py")
        git(self.origin, "commit", "-qm", "advance to B")

    def tearDown(self):
        self._tmp.cleanup()

    MAIN = "def add(a, b):\n    return a + b\n"

    def _tar(self, files, name="ws.tar"):
        path = os.path.join(self.tmp, name)
        with tarfile.open(path, "w"):
            pass
        for rel, data in files.items():
            add_member(path, f"worker-repo/{rel}", data=data)
        return path

    def _apply(self, files, base_commit=None, name="ws.tar"):
        return apply_to_branch(
            tar_path=self._tar(files, name), repo_url=self.origin,
            branch="worker/snap", workdir=os.path.join(self.tmp, "clone"),
            repo_dir="worker-repo", base_commit=base_commit,
        )

    def test_delivery_is_anchored_on_the_snapshot_not_the_live_head(self):
        # The workspace reflects A. Anchoring the delivery on A means the
        # branch of delivery is built from A (B's b_only.py is simply not part
        # of it), and the live main -- where B lives -- keeps b_only.py
        # untouched. Without a base, the delivery would be built over B and
        # `_drop_removed` would DELETE b_only.py from the delivered branch.
        result = self._apply({"main.py": self.MAIN}, base_commit=self.base_a)
        clone = result["clone_dir"]
        # B's file is not part of the A-anchored delivery branch...
        self.assertFalse(os.path.exists(os.path.join(clone, "b_only.py")))
        self.assertNotIn("b_only.py", result["diff"])
        # ...but the live main that B advanced is never mutated.
        main = git(clone, "show", "origin/main:b_only.py").stdout
        self.assertIn("ADDED", main)

    def test_delivery_reports_when_the_live_branch_has_moved(self):
        # The signal a later user/harness needs to reconcile: the remote main
        # is no longer the point this task was built from.
        result = self._apply({"main.py": self.MAIN}, base_commit=self.base_a)
        self.assertTrue(result["base_moved"])

    def test_delivery_without_a_base_still_uses_the_live_head(self):
        # Legacy records carry no base_commit; those keep the old behaviour
        # (deliver against the current remote main, so a file the branch
        # added while the task ran is dropped).
        result = self._apply({"main.py": self.MAIN})
        clone = result["clone_dir"]
        self.assertFalse(os.path.exists(os.path.join(clone, "b_only.py")))
        self.assertFalse(result["base_moved"])



class DeliveryCommitScopeTest(unittest.TestCase):
    """The delivery commit holds exactly the workspace, and nothing else.

    Staging by top-level pathspec (S9) protects the user's untracked files
    at the clone root, but a scratch file inside a TRACKED directory is
    still swept into the commit: `git add -A` on a directory pathspec adds
    whatever is untracked under it. The commit then turns a file the user
    merely dropped in the clone into reviewed history.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.origin = make_origin(self.tmp)
        package = os.path.join(self.origin, "package")
        os.makedirs(package)
        with open(os.path.join(package, "mod.py"), "w") as fh:
            fh.write("VALUE = 1\n")
        with open(os.path.join(self.origin, "del.py"), "w") as fh:
            fh.write("OLD = True\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-qm", "package + del")

    def tearDown(self):
        self._tmp.cleanup()

    MAIN = "def add(a, b):\n    return a + b\n"

    def _apply(self, files, branch, name):
        path = os.path.join(self.tmp, name)
        with tarfile.open(path, "w"):
            pass
        for rel, data in files.items():
            add_member(path, f"worker-repo/{rel}", data=data)
        return apply_to_branch(
            tar_path=path, repo_url=self.origin, branch=branch,
            workdir=os.path.join(self.tmp, "clone"),
            repo_dir="worker-repo",
        )

    def test_user_scratch_inside_a_tracked_dir_is_not_committed(self):
        first = self._apply({"main.py": self.MAIN,
                             "package/mod.py": "VALUE = 2\n"}, "worker/r1",
                            "ws1.tar")
        scratch = os.path.join(first["clone_dir"], "package", "notes.md")
        with open(scratch, "w") as fh:
            fh.write("private\n")
        self._apply({"main.py": self.MAIN, "del.py": "OLD = True\n",
                     "package/mod.py": "VALUE = 3\n"}, "worker/r2",
                    "ws2.tar")
        shown = git(os.path.join(self.tmp, "clone"), "show", "--stat",
                    "HEAD")
        self.assertNotIn("notes.md", shown.stdout, shown.stdout)
        self.assertTrue(os.path.exists(scratch),
                        "the user's scratch file was deleted from the clone")
        self.assertIn("VALUE = 3",
                      git(os.path.join(self.tmp, "clone"), "show",
                          "HEAD:package/mod.py").stdout)

    def test_a_deletion_by_the_agent_still_reaches_the_commit(self):
        # ws omits package/mod.py entirely: it must be deleted in the commit.
        self._apply({"main.py": self.MAIN, "del.py": "OLD = True\n"},
                    "worker/r1", "ws1.tar")
        clone = os.path.join(self.tmp, "clone")
        shown = git(clone, "show", "--stat", "HEAD")
        self.assertIn("package/mod.py", shown.stdout, shown.stdout)
        self.assertFalse(os.path.exists(os.path.join(clone, "package",
                                                     "mod.py")))

    def test_rerunning_the_same_branch_starts_clean(self):
        self._apply({"main.py": self.MAIN, "package/mod.py": "VALUE = 2\n",
                     "del.py": "OLD = True\n"}, "worker/r1", "ws1.tar")
        self._apply({"main.py": self.MAIN, "package/mod.py": "VALUE = 2\n",
                     "del.py": "OLD = True\n"}, "worker/r1", "ws2.tar")
        clone = os.path.join(self.tmp, "clone")
        self.assertEqual(git(clone, "status", "--short").stdout, "")
