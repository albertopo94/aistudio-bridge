"""G9 (+G16): a resumed task must say *which* clock expired, and stop.

`continue` against a dead sandbox used to surface the raw API error
(``Interactions API error 404: <body>``). The user could not tell that the
sandbox dies by design after 7 days, nor that the task is unrecoverable, so
the natural reaction was to retry forever.

Two clocks expire and they are different (G16): the sandbox holds the files
for 7 days, while the free tier keeps an interaction for 1 day. A 404 on a
resumed task is reported as an expired sandbox; the message carries the
concrete budget from the record, and never claims a number the API did not
give.

Rules under test:
1. A 404 while resuming is an expired sandbox, not a crash: exit path is a
   normal result with status ``expired``, never a traceback.
2. The message names the TTL that ran out and states that the task cannot be
   resumed.
3. The task is marked ``expired`` in state, so it is no longer offered as
   resumable.
4. The stored budget value is echoed when the record has it (concrete advice),
   and omitted cleanly when the record has none (never invent a number).
5. A 404 is terminal here: no retry, no second request.
6. The same friendly path covers a task whose budget ran out (``incomplete``)
   and which is resumed after the sandbox died — the ordinary way to meet this
   error.
7. Other API errors keep travelling as they did: only 404 becomes ``expired``.
"""

import json
import tempfile
import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.runner import TaskRunner
    from remote_worker.state import TaskStore
    from remote_worker.google_client import InteractionsClient
except ImportError:
    TaskRunner = None

NOT_FOUND_BODY = json.dumps({
    "error": {
        "code": 404,
        "message": "Requested resource was not found or your project does not "
                   "have permission to access it.",
        "status": "NOT_FOUND",
    }
})


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return '{"ok": true}'


class ExpiredSandboxTest(unittest.TestCase):
    def setUp(self):
        if TaskRunner is None:
            self.fail("RED: remote_worker not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)

    def _record_incomplete(self, *, task_id="t_100", budget=250000,
                           with_repo_dir=True):
        record = {
            "task_id": task_id,
            "kind": "run",
            "status": "incomplete",
            "interaction_id": "v_old",
            "environment_id": "env_dead",
            "repo_url": "https://github.com/o/repo-x",
            "branch": "worker/repo-x-100",
            "model": "gemini-3.8-flash",
            "task": "add divide",
        }
        if with_repo_dir:
            record["repo_dir"] = "/workspace/repo-x"
        if budget is not None:
            record["budget"] = budget
        self.store.record(record)
        return task_id

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=self.store,
            workdir=self._tmp.name,
        )

    # Rules 1, 2, 3, 4, 5, 6
    def test_continue_reports_expired_sandbox_instead_of_raw_404(self):
        task_id = self._record_incomplete()
        with FakeInteractionsServer() as srv:
            # The pre-check read succeeds: the conversation is still there,
            # only the sandbox is gone. The continuation then gets the 404.
            srv.get_replies.append((200, json.dumps(
                {"id": "v_old", "object": "interaction", "status": "incomplete",
                 "usage": {}, "steps": []}).encode()))
            srv.replies = [(404, json.loads(NOT_FOUND_BODY))]
            result = self._runner(srv).continue_task(task_id=task_id)
            requests = list(srv.requests)

        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["task_id"], task_id)

        message = result["report"]
        self.assertIn("expired", message.lower())
        self.assertIn("7", message)                       # the sandbox TTL
        self.assertIn("cannot", message.lower())          # unrecoverable
        self.assertIn("250000", message)                  # concrete budget
        self.assertNotIn("Interactions API error 404", message)

        # Rule 3: no longer resumable.
        self.assertEqual(self.store.get(task_id)["status"], "expired")
        # Rule 5: one pre-check plus exactly one attempt at the continuation.
        self.assertEqual([r["method"] for r in requests], ["GET", "POST"])

    # The pre-check can find the conversation itself already gone (free tier
    # keeps one for a day, which is usually the first clock to run out).
    # Same verdict, one request fewer.
    def test_gone_conversation_is_reported_expired_without_an_attempt(self):
        task_id = self._record_incomplete(task_id="t_250")
        with FakeInteractionsServer() as srv:
            srv.get_replies = [(404, NOT_FOUND_BODY.encode())]
            result = self._runner(srv).continue_task(task_id=task_id)
            requests = list(srv.requests)
        self.assertEqual(result["status"], "expired")
        self.assertEqual([r["method"] for r in requests], ["GET"])
        self.assertEqual(self.store.get(task_id)["status"], "expired")

    # Rule 4: never invent a number the record does not have.
    def test_message_omits_the_budget_when_the_record_has_none(self):
        task_id = self._record_incomplete(task_id="t_200", budget=None)
        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                {"id": "v_old", "object": "interaction", "status": "incomplete",
                 "usage": {}, "steps": []}).encode()))
            srv.replies = [(404, json.loads(NOT_FOUND_BODY))]
            result = self._runner(srv).continue_task(task_id=task_id)
        self.assertEqual(result["status"], "expired")
        self.assertNotIn(" of ", result["report"])

    # Rule 7: a 500 is still an API error, not an expiry.
    def test_other_api_errors_still_raised(self):
        task_id = self._record_incomplete(task_id="t_300")
        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                {"id": "v_old", "object": "interaction", "status": "incomplete",
                 "usage": {}, "steps": []}).encode()))
            srv.reply_status = 500
            srv.reply_body = {"error": {"message": "backend exploded",
                                        "status": "INTERNAL"}}
            with self.assertRaises(Exception) as ctx:
                self._runner(srv).continue_task(task_id=task_id)
        self.assertIn("500", str(ctx.exception))
        # The task keeps its resumable status: a server hiccup is not an expiry.
        self.assertEqual(self.store.get(task_id)["status"], "incomplete")

    # Rule 3 from the user's side: status does not offer it as resumable.
    def test_cli_does_not_tell_the_user_to_continue_an_expired_task(self):
        from remote_worker import cli
        import io
        from contextlib import redirect_stdout, redirect_stderr

        task_id = self._record_incomplete(task_id="t_400")
        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                {"id": "v_old", "object": "interaction", "status": "incomplete",
                 "usage": {}, "steps": []}).encode()))
            srv.replies = [(404, json.loads(NOT_FOUND_BODY))]
            runner = self._runner(srv)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["continue", task_id], runner=runner)
        printed = out.getvalue()
        # The requested resume could not happen: non-zero, like a failed task.
        self.assertEqual(code, 3)
        self.assertIn("expired", printed)
        # The useless advice must be gone: continuing an expired task never works.
        self.assertNotIn("remote-worker continue", printed)


if __name__ == "__main__":
    unittest.main()
