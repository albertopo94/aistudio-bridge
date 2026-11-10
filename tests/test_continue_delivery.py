"""Hardening S3: a continuation finishes the trip its sibling start began.

`run` delivered the work: download the workspace, apply it to a local
branch, record what landed. `continue_task` resumed the interaction, wrote
the status and returned -- so a code task that hit its budget, continued
and then COMPLETED in Google never came home, even though it had been paid
for. The review notes (puntos a mejorar.md, Crítico 3) name it as the
functional inconsistency it is, and ask for one reusable delivery operation
shared by `run` and `continue_task`.

Rules under test:

1. A continuation whose interaction ends `completed` delivers exactly like a
   run: the workspace is downloaded, applied to the local branch, the clone
   is recorded, and the status stays `completed`.
2. A continuation whose delivery fails records `undelivered` -- never
   `completed` -- so `clean` keeps the sandbox holding the only copy.
3. The delivery honours the same `--run-tests` policy: not executed unless
   asked, and the report says so.
4. A continuation that does not complete (budget/incomplete) delivers
   nothing and creates no branch.
"""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest

from remote_worker.google_client import (EnvironmentsClient, FilesClient,
                                         InteractionsClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from tests.fake_interactions import FakeInteractionsServer
from tests.test_delivery_failure import _make_origin, _worker_tar_bytes

ENV_ID = "env_cont"


def _interaction(status="completed"):
    return {"id": "v1_cont", "status": status, "environment_id": ENV_ID,
            "usage": {"total_tokens": 5000},
            "steps": [{"type": "model_output",
                       "content": [{"type": "text", "text": "done"}]}]}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


class ContinueDeliversTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = _make_origin(self.tmp)
        self.store = TaskStore(project="proj", root=self.tmp)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=FilesClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store, workdir=self.tmp,
        )

    def _seed_incomplete(self):
        self.store.record({
            "task_id": "t_cont", "kind": "run", "status": "incomplete",
            "interaction_id": "v1_prev", "environment_id": ENV_ID,
            "repo_url": self.origin, "repo_dir": "/workspace/origin",
            "branch": "worker/cont", "model": "gemini-3.8-flash",
            "budget": 9000,
        })

    def _serve(self, srv, *, final_status="completed", tar=True,
               tar_status=200):
        # The G13 pre-check reads the running interaction; then the
        # continuation POST; then the workspace download GET.
        srv.get_replies = [(200, json.dumps({"status": "completed"}).encode())]
        if tar:
            srv.get_replies.append((tar_status, _worker_tar_bytes()))
        srv.reply_body = _interaction(final_status)

    # Rule 1: completed continuation lands on the branch.
    def test_a_completed_continuation_delivers(self):
        self._seed_incomplete()
        with FakeInteractionsServer() as srv:
            self._serve(srv)
            result = self._runner(srv).continue_task(task_id="t_cont")
        self.assertEqual(result["status"], "completed")
        clone = os.path.join(self.tmp, "t_cont", "clone")
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        log = subprocess.run(["git", "-C", clone, "log", "--oneline"],
                             capture_output=True, text=True)
        self.assertIn("worker/cont", log.stdout)
        record = self.store.get("t_cont")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["clone_dir"], clone)

    # Rule 2: a failed delivery is undelivered, not completed.
    def test_a_failed_continuation_delivery_is_undelivered(self):
        self._seed_incomplete()
        with FakeInteractionsServer() as srv:
            self._serve(srv, tar_status=500)
            result = self._runner(srv).continue_task(task_id="t_cont")
        self.assertEqual(result["status"], "undelivered")
        self.assertEqual(self.store.get("t_cont")["status"], "undelivered")
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "t_cont",
                                                    "clone", ".git")))

    # Rule 3: the same tests policy as a run.
    def test_tests_are_not_run_unless_asked(self):
        self._seed_incomplete()
        with FakeInteractionsServer() as srv:
            self._serve(srv)
            result = self._runner(srv).continue_task(task_id="t_cont")
        self.assertIsNone(result.get("tests_rc"))
        self.assertTrue(result.get("tests_skipped"))

    # Rule 4: nothing to deliver before completion.
    def test_an_incomplete_continuation_delivers_nothing(self):
        self._seed_incomplete()
        with FakeInteractionsServer() as srv:
            self._serve(srv, final_status="incomplete", tar=False)
            result = self._runner(srv).continue_task(task_id="t_cont")
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "t_cont",
                                                    "clone", ".git")))
        self.assertNotIn("clone_dir", self.store.get("t_cont"))


if __name__ == "__main__":
    unittest.main()