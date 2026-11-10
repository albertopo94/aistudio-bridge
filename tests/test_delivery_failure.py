"""F1: a failed return delivery must never leave a task recorded `completed`.

The live measurement of the download ceiling (odd/tasks/remote-worker-gaps.md
L39) caught the product lying to its own state: the agent's turn completed and
spent its tokens, then the workspace tar failed with a service 500, the raw
error escaped `run`, no branch existed -- yet the task record said
`completed`. Two consequences, both proven live (task t_1794045414):

- the user reviews a "completed" task that delivered nothing, and
- `clean --yes` treats `completed` as "the sandbox is no longer needed" and
  deletes the environment that still holds the ONLY copy of the bytes.

Rules under test:

1. When the return delivery fails (the download, or applying it), the task is
   recorded `undelivered` -- not `completed`, not `failed` -- and `run` returns
   that status with the service's error, instead of raising through the state
   it just wrote.
2. `undelivered` keeps the sandbox: `clean --yes` keeps (does not delete) the
   environment of an undelivered task, because those bytes are the only copy.
3. An `undelivered` task is not continuable: the agent's turn finished and was
   paid for; recovery is `fetch`, not another budget.
4. The CLI says the recovery path, not just the failure: `fetch <task>
   --list` and `--path`, the 7-day TTL, exit 3 (a run that delivered nothing
   is not success).
5. The success path is untouched: with a real tar and a real local origin,
   delivery still lands on a branch and records `completed`.
"""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from remote_worker import cli
from remote_worker.google_client import (EnvironmentsClient, FilesClient,
                                         InteractionsClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore

ENV_ID = "env_stuck"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "-C", repo, *args], capture_output=True, text=True,
                   check=True)


def _make_origin(tmp):
    """A real git repo with one commit; its path doubles as a clone URL."""
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    _git(origin, "init", "-q")
    with open(os.path.join(origin, "main.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a + b\n")
    _git(origin, "add", "main.py")
    _git(origin, "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "initial")
    return origin


def _worker_tar_bytes():
    """A workspace tar shaped like a sandbox that cloned this repo.

    The service clones the repository itself, so the workspace carries the
    whole tree the origin holds -- including its tests. That matters since
    hardening S4: a delivery removes tracked files the workspace no longer
    holds, so a tar missing `tests/` would legitimately delete them.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        def add(name, payload, mode=0o644):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))

        add("origin/main.py",
            b"def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n"
            b"    return a * b\n")
        add("origin/tests/test_main.py",
            b"import sys, os\n"
            b"sys.path.insert(0, os.path.dirname(os.path.dirname(\n"
            b"    os.path.abspath(__file__))))\n"
            b"import main\n\n\n"
            b"def test_add():\n"
            b"    assert main.add(1, 2) == 3\n")
    return buf.getvalue()


def _interaction(status="completed"):
    return {"id": "v1_delivery", "status": status,
            "environment_id": ENV_ID, "usage": {"total_tokens": 93446},
            "steps": []}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


from tests.fake_interactions import FakeInteractionsServer  # noqa: E402


class DeliveryFailureTest(unittest.TestCase):
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
            dispatcher=StubMemory(), store=self.store,
            workdir=self.tmp,
        )

    def _run_against(self, srv):
        return self._runner(srv).run(task="do the thing",
                                     repo_url=self.origin)

    # Rule 1: the shape of the failure, live-captured (t_1794045414).
    def test_a_failed_tar_download_records_undelivered_not_completed(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(500, b'{"error":{"message":"Internal error '
                                   b'encountered.","code":"api_error"}}')]
            result = self._run_against(srv)
        self.assertEqual(result["status"], "undelivered", result)
        # The service's own words travel to the user, not a silent swallow.
        self.assertIn("500", str(result.get("delivery_error")))
        stored = self.store.get(result["task_id"])
        self.assertEqual(stored["status"], "undelivered")
        self.assertNotEqual(stored["status"], "completed")
        # The agent's report is not lost: what it said is still the best
        # information about the task itself.
        self.assertIsNone(result.get("clone_dir"))

    # Rule 1b: the tar arriving corrupt is the same class of failure: the
    # delivery died, the bytes are safe in the sandbox.
    def test_an_unreadable_tar_is_undelivered_too(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, b"this is not a tar, it is a JSON body")]
            result = self._run_against(srv)
        self.assertEqual(result["status"], "undelivered", result)
        self.assertEqual(self.store.get(result["task_id"])["status"],
                         "undelivered")

    # Rule 2: the data guard. `completed` made this environment a `clean
    # --yes` candidate; the sandbox is the only copy of the work.
    def test_clean_keeps_the_sandbox_of_an_undelivered_task(self):
        self.store.record({"task_id": "t_done", "status": "completed",
                           "environment_id": "env_done"})
        self.store.record({"task_id": "t_stuck", "status": "undelivered",
                           "environment_id": ENV_ID})
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = json.dumps({"environments": [
                {"id": "env_done", "status": "active"},
                {"id": ENV_ID, "status": "active"}]}).encode()
            srv.delete_replies = [(200, b"{}")]
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["clean", "--yes"],
                                runner=self._runner(srv))
        deleted = [r["path"] for r in srv.requests if r["method"] == "DELETE"]
        self.assertEqual(deleted, ["/v1beta/environments/env_done"],
                         f"clean deleted the undelivered sandbox: {deleted}")
        self.assertEqual(code, 0)
        self.assertIn(ENV_ID, out.getvalue())  # named as kept

    # Rule 3: the turn is finished and paid; `continue` would spend again.
    def test_an_undelivered_task_cannot_be_continued(self):
        self.store.record({"task_id": "t_stuck", "status": "undelivered",
                           "environment_id": ENV_ID, "kind": "run"})
        with FakeInteractionsServer() as srv:
            with self.assertRaises(ValueError) as caught:
                self._runner(srv).continue_task(task_id="t_stuck")
        self.assertIn("undelivered", str(caught.exception))
        self.assertEqual(srv.requests, [])  # refused before any request

    # Rule 4: the CLI prints the recovery, and does not call it success.
    def test_the_cli_names_fetch_as_the_way_back(self):
        class StubRunner:
            def run(self, **kwargs):
                return {"task_id": "t_9", "status": "undelivered",
                        "tokens": 93446, "budget": 60000,
                        "branch": "worker/o-9",
                        "report": "### what the agent said",
                        "delivery_error": "Interactions API error 500: boom",
                        "environment_id": ENV_ID}

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["run", "t", "--repo",
                             "https://github.com/o/r"], runner=StubRunner())
        text = out.getvalue() + err.getvalue()
        self.assertEqual(code, 3, text)
        self.assertIn("undelivered", text)
        self.assertIn("t_9 --list", text)          # the exact recovery command
        self.assertIn("t_9 --path", text)
        self.assertIn("7-day", text)               # how long the bytes last
        # The success sentence must not appear: nothing can be merged.
        self.assertNotIn("merge the branch", text)
        self.assertNotIn("tests:", text)

    # Rule 5: the healthy path still lands -- real tar, real clone, real
    # apply. This is what the try/except must not swallow.
    def test_a_good_tar_still_delivers_to_a_branch(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            result = self._run_against(srv)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(self.store.get(result["task_id"])["status"],
                         "completed")
        self.assertTrue(os.path.isdir(os.path.join(
            self.tmp, "clone", ".git")), "no clone was made")
        # S9: the delivery is a commit on its branch, so the index is clean
        # and the work is visible in history rather than staged forever.
        shown = subprocess.run(
            ["git", "-C", os.path.join(self.tmp, "clone"), "show",
             "--stat", "--oneline", "HEAD"], capture_output=True, text=True)
        self.assertIn("main.py", shown.stdout)
        self.assertEqual(subprocess.run(
            ["git", "-C", os.path.join(self.tmp, "clone"), "status",
             "--short"], capture_output=True, text=True).stdout, "")
        # The workspace tar is transient: it does not accumulate per task.
        leftovers = [f for f in os.listdir(self.tmp)
                     if f.endswith(".tar")]
        self.assertEqual(leftovers, [], leftovers)


if __name__ == "__main__":
    unittest.main()
