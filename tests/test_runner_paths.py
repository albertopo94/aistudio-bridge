"""Runner rules that do not need the network."""

import os
import unittest

try:
    from remote_worker.runner import TaskRunner
    from remote_worker.state import DEFAULT_ROOT
except ImportError:
    TaskRunner = None


class TestWorkdir(unittest.TestCase):
    def setUp(self):
        if TaskRunner is None:
            self.fail("RED: remote_worker.runner not implemented yet")

    # A task recorded in one session must be resumable in the next, so the
    # clone directory cannot live in the process temp directory.
    def test_workdir_is_stable_across_sessions(self):
        runner = TaskRunner(project="proj", api_key="not-used")
        self.assertTrue(runner.workdir.startswith(DEFAULT_ROOT),
                        runner.workdir)
        self.assertEqual(os.path.basename(runner.workdir), "work")

    def test_explicit_workdir_wins(self):
        runner = TaskRunner(project="proj", api_key="not-used",
                            workdir="/tmp/explicit-clone")
        self.assertEqual(runner.workdir, "/tmp/explicit-clone")


if __name__ == "__main__":
    unittest.main()


class TestTestCloneIsIdempotent(unittest.TestCase):
    """A second run on the same project must not crash on a stale tests dir."""

    def setUp(self):
        if TaskRunner is None:
            self.fail("RED: remote_worker.runner not implemented yet")

    def test_run_cleans_stale_tests_dir(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            runner = TaskRunner(project="p", api_key="k", workdir=tmp)
            clone_dir = os.path.join(tmp, "clone")
            os.makedirs(clone_dir)
            with open(os.path.join(clone_dir, "main.py"), "w") as fh:
                fh.write("x=1")
            # a stale tests dir that must be replaced, not merged
            stale = os.path.join(tmp, "clone.tests")
            os.makedirs(stale)
            marker = os.path.join(stale, "STALE")
            with open(marker, "w") as fh:
                fh.write("old")
            tests_clone = runner._prepare_tests_clone(clone_dir)
            self.assertFalse(
                os.path.exists(marker),
                "stale tests dir survived; it should have been replaced",
            )
            self.assertTrue(os.path.exists(os.path.join(tests_clone, "main.py")))
