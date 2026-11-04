"""WU-4b: tests for remote_worker.cli.

The CLI is tested with an injected runner, so no network and no key are
needed. Rules under test:

1. ``--version`` prints the project version from VERSION.
2. A model below the floor is a usage error (rc 2) naming the model.
3. A task that hits its budget is not a failure: the CLI exits 0 and tells
   the user how to continue.
4. A completed task reports status, tokens, branch and the changed files.
5. An unknown task id exits non-zero with a message, never a traceback.
"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

try:
    from remote_worker import cli
except ImportError:
    cli = None


class StubRunner:
    """Records calls and returns a canned result."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"task_id": "t_1", "status": "completed"}

    def run(self, **kwargs):
        self.calls.append(("run", kwargs))
        return dict(self.result)

    def continue_task(self, **kwargs):
        self.calls.append(("continue", kwargs))
        return dict(self.result)

    def status(self, task_id=None):
        self.calls.append(("status", task_id))
        if task_id == "missing":
            raise KeyError("unknown task 'missing'")
        return [{"task_id": "t_1", "status": "completed"}] if task_id is None \
            else dict(self.result)

    def cancel(self, task_id):
        self.calls.append(("cancel", task_id))
        return dict(self.result)


def invoke(argv, runner):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, runner=runner)
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker.cli not implemented yet")

    # Rule 1
    def test_version(self):
        code, out, _ = invoke(["--version"], StubRunner())
        self.assertEqual(code, 0)
        self.assertRegex(out.strip(), r"^\d+\.\d+\.\d+$")

    # Rule 2
    def test_model_below_floor_is_usage_error(self):
        runner = StubRunner()
        with self.assertRaises(SystemExit) as ctx:
            invoke(["run", "add a thing", "--repo", "u", "--model",
                    "gemini-3.5-flash-lite"], runner)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(runner.calls, [])

    # Rule 3
    def test_incomplete_is_not_a_failure(self):
        runner = StubRunner({
            "task_id": "t_1", "status": "incomplete", "tokens": 120,
            "budget": 100,
        })
        code, out, _ = invoke(
            ["run", "a task", "--repo", "https://x/y"], runner
        )
        self.assertEqual(code, 0)
        self.assertIn("incomplete", out)
        self.assertIn("continue t_1", out)

    # Rule 4
    def test_completed_reports_branch_and_changes(self):
        runner = StubRunner({
            "task_id": "t_1", "status": "completed", "tokens": 5000,
            "branch": "worker/abc", "clone_dir": "/tmp/clone",
            "changed": ["M  main.py"], "tests_rc": 0,
        })
        code, out, _ = invoke(
            ["run", "a task", "--repo", "https://x/y"], runner
        )
        self.assertEqual(code, 0)
        self.assertIn("completed", out)
        self.assertIn("worker/abc", out)
        self.assertIn("main.py", out)
        self.assertIn("5000", out)

    # Rule 5
    def test_unknown_task_exits_nonzero(self):
        code, _, err = invoke(["status", "missing"], StubRunner())
        self.assertEqual(code, 1)
        self.assertIn("missing", err)

    def test_status_without_id_lists_tasks(self):
        code, out, _ = invoke(["status"], StubRunner())
        self.assertEqual(code, 0)
        self.assertIn("t_1", out)

    def test_run_requires_repo(self):
        with self.assertRaises(SystemExit) as ctx:
            invoke(["run", "a task"], StubRunner())
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

class TestProjectDefault(unittest.TestCase):
    """The default Engram project is the directory the user runs in."""

    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker.cli not implemented yet")

    def test_default_project_is_cwd_not_repo_name(self):
        import argparse
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                args = argparse.Namespace(repo="https://x/worker-poc-toy")
                self.assertEqual(cli._guess_project(args),
                                 os.path.basename(tmp))
            finally:
                os.chdir(previous)


class TestOfflineCommands(unittest.TestCase):
    """Reading local state must not require an API key."""

    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker.cli not implemented yet")

    def test_status_needs_no_api_key(self):
        import os

        saved = os.environ.pop("GEMINI_API_KEY", None)
        try:
            code, _, err = invoke(["status"], None)
        finally:
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved
        self.assertEqual(code, 0, err)
        self.assertNotIn("GEMINI_API_KEY", err)
