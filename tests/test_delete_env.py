"""T25 / L42: `run` can release its sandbox, and never by accident.

The user's decision (odd/tasks/remote-worker-gaps.md L42): the product asks,
and deletes only when the work is not lost. The sandbox holds the only copy
of the bytes until delivery succeeds, so:

Rules under test:

1. `--delete-env` after a VERIFIED delivery: the environment is deleted
   exactly once, and the run stays `completed`.
2. `--delete-env` without a delivery (undelivered): no DELETE is sent -- the
   sandbox is the only copy of the work -- and the run does not pretend the
   flag was honoured.
3. Without the flag, after a successful delivery, the CLI poses the question
   and names how to act on it (`remote-worker clean`), sending no DELETE.
4. With the flag absent but a terminal attached, the question is asked and
   answered: "y" deletes, anything else (the default) keeps.
5. A failed delete does not turn a delivered run into a failure: the run is
   `completed`, the branch is there, and the error is named.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from remote_worker import cli
from remote_worker.google_client import (EnvironmentsClient, FilesClient,
                                         InteractionsClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from tests.fake_interactions import FakeInteractionsServer
from tests.test_delivery_failure import _make_origin, _worker_tar_bytes

ENV_ID = "env_del"


def _interaction(status="completed"):
    return {"id": "v1_del", "status": status, "environment_id": ENV_ID,
            "usage": {"total_tokens": 93446}, "steps": []}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


class DeleteEnvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = _make_origin(self.tmp)
        # `run` executes the project's own tests after applying (that is the
        # DEFAULT_TEST_COMMAND), so the fixture must carry a passing one for
        # a clean exit code; the delivered main.py keeps `add` intact.
        tests_dir = os.path.join(self.origin, "tests")
        os.makedirs(tests_dir)
        with open(os.path.join(tests_dir, "test_main.py"), "w") as fh:
            fh.write(
                "import os\n"
                "import sys\n"
                "sys.path.insert(0, os.path.dirname(\n"
                "    os.path.dirname(os.path.abspath(__file__))))\n"
                "\n"
                "import main\n"
                "\n\n"
                "def test_add():\n"
                "    assert main.add(1, 2) == 3\n"
            )
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t",
             "-C", self.origin, "add", "tests/test_main.py"],
            capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t",
             "-C", self.origin, "commit", "-qm", "Add the tests"],
            capture_output=True, text=True, check=True)
        self.store = TaskStore(project="proj", root=self.tmp)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=FilesClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store,
            workdir=self.tmp,
        )

    def _deletes(self, srv):
        return [r["path"] for r in srv.requests if r["method"] == "DELETE"]

    # Rule 1: delivered + flag -> one DELETE, run still completed.
    def test_flag_deletes_after_a_verified_delivery(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            srv.delete_replies = [(200, b"{}")]
            result = self._runner(srv).run(
                task="x", repo_url=self.origin, delete_env=True)
            deletes = self._deletes(srv)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result.get("env_released"))
        self.assertEqual(deletes, [f"/v1beta/environments/{ENV_ID}"])

    # Rule 2: undelivered -> the flag does not cost the user their bytes.
    def test_flag_never_deletes_an_undelivered_sandbox(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(500, b'{"error":{"message":"boom"}}')]
            result = self._runner(srv).run(
                task="x", repo_url=self.origin, delete_env=True)
            deletes = self._deletes(srv)
        self.assertEqual(result["status"], "undelivered")
        self.assertEqual(deletes, [])
        self.assertFalse(result.get("env_released", False))

    # Rule 3: no flag, no terminal -> the question is posed, nothing deleted.
    def test_without_the_flag_the_cli_poses_the_question(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["run", "x", "--repo", self.origin],
                                runner=self._runner(srv))
            deletes = self._deletes(srv)
        self.assertEqual(code, 0)
        self.assertEqual(deletes, [])
        printed = out.getvalue()
        self.assertIn(ENV_ID, printed)          # the sandbox is named
        self.assertIn("remote-worker clean", printed)  # how to act on it

    # Rule 4: no flag, terminal attached -> asked, "y" deletes.
    def test_in_a_terminal_the_prompt_deletes_on_yes(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            srv.delete_replies = [(200, b"{}")]
            with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", return_value="y"):
                with redirect_stdout(io.StringIO()), \
                        redirect_stderr(io.StringIO()):
                    code = cli.main(["run", "x", "--repo", self.origin],
                                    runner=self._runner(srv))
            deletes = self._deletes(srv)
        self.assertEqual(code, 0)
        self.assertEqual(deletes, [f"/v1beta/environments/{ENV_ID}"])

    # Rule 4b: the default answer keeps the sandbox.
    def test_in_a_terminal_enter_keeps_the_sandbox(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", return_value=""):
                with redirect_stdout(io.StringIO()), \
                        redirect_stderr(io.StringIO()):
                    code = cli.main(["run", "x", "--repo", self.origin],
                                    runner=self._runner(srv))
            deletes = self._deletes(srv)
        self.assertEqual(code, 0)
        self.assertEqual(deletes, [])

    # Rule 5: the delete failing is not the delivery failing.
    def test_a_failed_delete_does_not_fail_the_run(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            srv.delete_replies = [
                (403, json.dumps({"error": {"message": "forbidden"}}).encode())]
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["run", "x", "--repo", self.origin,
                                 "--delete-env"],
                                runner=self._runner(srv))
        self.assertEqual(code, 0)          # the delivery is the outcome
        self.assertIn(ENV_ID, err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()