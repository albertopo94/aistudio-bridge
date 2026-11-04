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
