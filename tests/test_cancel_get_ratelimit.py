"""Reach the service: cancel remotely, read one interaction, name a rate limit.

Three gaps share one client and one CLI error path, so they are pinned together.

G12 — cancel server-side
1. ``cancel_interaction`` POSTs to ``/v1beta/interactions/{id}/cancel`` (a path
   segment, not the ``:cancel`` custom verb) with no request body.
2. ``cancel`` on a task with an interaction id also calls the service and says so.
3. A failing remote cancel never loses the local cancel: the task is canceled,
   the command exits 0, and the line says the service was not reached.
4. A task with no recorded interaction, and a machine with no API key, stay
   local-only and send no request at all.

G18 — read one interaction
5. ``get_interaction`` GETs ``/v1beta/interactions/{id}`` and returns the parsed
   interaction.
6. ``status`` without ``--live`` sends no request and prints exactly what it
   printed before the flag existed (it must keep working with no key).
7. ``status --live`` prints the service's own status on a ``live status:`` line.
8. ``status --live`` on an id the service no longer has says the record is
   stale, with a pinned exit code and no traceback.

G19 — a rate limit that reads as one
9. A 429 exits with the rate-limit hint, never a traceback, and explains why
   there is no automatic retry (a continuation is not idempotent).
10. A non-429 error keeps its own message and gets no rate-limit hint.
11. The streaming path keeps the HTTP status of a failed request instead of
    flattening it to 0, while an in-stream ``error`` event (which has no HTTP
    status) stays distinguishable.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker import cli
    from remote_worker.google_client import InteractionsClient, InteractionsError
    from remote_worker.runner import TaskRunner
    from remote_worker.state import TaskStore
except ImportError:  # pragma: no cover
    cli = None

API = "antigravity-preview-09-2026"


def _interaction(status="completed", iid="v1", steps=None):
    return {"id": iid, "object": "interaction", "status": status,
            "model": API, "usage": {"total_tokens": 10},
            "steps": steps if steps is not None else
            [{"type": "model_output", "content": [{"type": "text",
                                                   "text": "hi"}]}]}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return '{"ok": true}'


def invoke(argv, runner):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, runner=runner)
    return code, out.getvalue(), err.getvalue()


class CancelAndGetClientTest(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker not implemented yet")

    def _client(self, base_url):
        return InteractionsClient(api_key="AQ.k", base_url=base_url)

    # Rule 1
    def test_cancel_uses_the_cancel_path_segment_without_a_body(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("cancelled")
            result = self._client(srv.base_url).cancel_interaction("v_9")
        req = srv.last_request
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/v1beta/interactions/v_9/cancel")
        self.assertEqual(req["body"], "")            # no body was sent
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_needs_an_interaction_id(self):
        with self.assertRaises(ValueError):
            self._client("http://127.0.0.1:1").cancel_interaction("")

    # Rule 5
    def test_get_interaction_reads_one_interaction(self):
        with FakeInteractionsServer() as srv:
            srv.reply_status = 200
            srv.reply_bytes = json.dumps(
                _interaction("in_progress", iid="v_7")).encode("utf-8")
            result = self._client(srv.base_url).get_interaction("v_7")
        req = srv.last_request
        self.assertEqual(req["method"], "GET")
        self.assertEqual(req["path"], "/v1beta/interactions/v_7")
        self.assertEqual(result["status"], "in_progress")

    def test_get_interaction_needs_an_interaction_id(self):
        with self.assertRaises(ValueError):
            self._client("http://127.0.0.1:1").get_interaction("")

    # Rule 11: the SSE path must not flatten the HTTP status.
    def test_stream_http_error_keeps_its_status(self):
        with FakeInteractionsServer() as srv:
            srv.reply_status = 429
            srv.reply_body = {"error": {"message": "Resource has exhausted "
                                                   "the quota",
                                        "status": "RESOURCE_EXHAUSTED"}}
            with self.assertRaises(InteractionsError) as ctx:
                self._client(srv.base_url).stream_interaction(
                    agent=API, input="go")
        self.assertEqual(ctx.exception.status, 429)

    # Rule 11: an in-stream error has no HTTP status and must stay 0.
    def test_stream_error_event_has_no_http_status(self):
        with FakeInteractionsServer() as srv:
            srv.sse_events = [{"event_type": "error",
                               "error": {"message": "upstream exploded"}}]
            with self.assertRaises(InteractionsError) as ctx:
                self._client(srv.base_url).stream_interaction(
                    agent=API, input="go")
        self.assertEqual(ctx.exception.status, 0)
        self.assertIn("upstream exploded", ctx.exception.body)


class RunnerCancelTest(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)

    def _record(self, task_id="t_1", with_interaction=True):
        record = {"task_id": task_id, "kind": "run", "status": "completed",
                  "environment_id": "env_1", "branch": "worker/x-1"}
        if with_interaction:
            record["interaction_id"] = "v_1"
        self.store.record(record)
        return task_id

    def _runner(self, srv, api_key="AQ.k"):
        # An empty key means no client at all, exactly as the CLI builds it:
        # TaskRunner creates clients lazily, so an unused key never needs one.
        client = (InteractionsClient(api_key=api_key, base_url=srv.base_url)
                  if api_key else None)
        return TaskRunner(
            project="proj", api_key=api_key,
            client=client,
            dispatcher=StubMemory(), store=self.store,
            workdir=self._tmp.name,
        )

    # Rule 2
    def test_cancel_reaches_the_service_when_it_can(self):
        task_id = self._record()
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("cancelled")
            result = self._runner(srv).cancel(task_id)
            path = srv.last_request["path"]
        self.assertEqual(path, "/v1beta/interactions/v_1/cancel")
        self.assertTrue(result["remote_canceled"])
        self.assertEqual(self.store.get(task_id)["status"], "canceled")

    # Rule 3
    def test_remote_failure_keeps_the_local_cancel(self):
        task_id = self._record(task_id="t_2")
        with FakeInteractionsServer() as srv:
            srv.reply_status = 400
            srv.reply_body = {"error": {"message": "interaction already done",
                                        "status": "FAILED_PRECONDITION"}}
            result = self._runner(srv).cancel(task_id)
        self.assertFalse(result["remote_canceled"])
        self.assertEqual(self.store.get(task_id)["status"], "canceled")
        self.assertTrue(result["remote_error"])

    # Rule 3 from the user's side: the printed line tells the truth.
    def test_cli_reports_which_cancel_happened(self):
        task_id = self._record(task_id="t_3")
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("cancelled")
            code, out, err = invoke(["cancel", task_id], self._runner(srv))
        self.assertEqual(code, 0)
        self.assertIn(task_id, out)
        self.assertIn("service", out.lower())
        self.assertNotIn("local tracking only", out)

        task_id = self._record(task_id="t_4")
        with FakeInteractionsServer() as srv:
            srv.reply_status = 400
            srv.reply_body = {"error": {"message": "nope", "status": "FAILED"}}
            code, out, err = invoke(["cancel", task_id], self._runner(srv))
        self.assertEqual(code, 0)
        self.assertIn("local", out.lower())
        self.assertNotIn("Traceback", err)

    # Rule 4: nothing to cancel remotely, so nothing is sent.
    def test_record_without_interaction_id_stays_local(self):
        task_id = self._record(task_id="t_5", with_interaction=False)
        with FakeInteractionsServer() as srv:
            result = self._runner(srv).cancel(task_id)
            requests = list(srv.requests)
        self.assertEqual(requests, [])
        self.assertFalse(result["remote_canceled"])
        self.assertEqual(self.store.get(task_id)["status"], "canceled")

    # Rule 4: no key means no call, and the command still works.
    def test_no_api_key_stays_local_and_sends_nothing(self):
        task_id = self._record(task_id="t_6")
        with FakeInteractionsServer() as srv:
            result = self._runner(srv, api_key="").cancel(task_id)
            requests = list(srv.requests)
        self.assertEqual(requests, [])
        self.assertFalse(result["remote_canceled"])
        self.assertEqual(self.store.get(task_id)["status"], "canceled")


class StatusLiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)
        self.store.record({"task_id": "t_50", "status": "incomplete",
                           "interaction_id": "v_50", "model": "gemini-3.8-flash",
                           "branch": "worker/x-50"})

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store,
            workdir=self._tmp.name,
        )

    # Rule 6
    def test_status_offline_sends_nothing(self):
        with FakeInteractionsServer() as srv:
            code, out, err = invoke(["status", "t_50"], self._runner(srv))
            requests = list(srv.requests)
        self.assertEqual(requests, [])
        self.assertEqual(code, 0)
        self.assertIn("status: incomplete", out)
        self.assertNotIn("live status", out)

    # Rule 7
    def test_status_live_prints_the_service_status(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = json.dumps(
                _interaction("completed", iid="v_50")).encode("utf-8")
            code, out, err = invoke(["status", "t_50", "--live"],
                                    self._runner(srv))
        self.assertEqual(code, 0)
        self.assertIn("live status: completed", out)
        self.assertIn("status: incomplete", out)   # local record still shown

    # Rule 8
    def test_status_live_on_a_gone_interaction_is_readable(self):
        with FakeInteractionsServer() as srv:
            srv.reply_status = 404
            srv.reply_bytes = json.dumps({"error": {
                "code": 404, "status": "NOT_FOUND",
                "message": "Requested resource was not found"}}).encode()
            code, out, err = invoke(["status", "t_50", "--live"],
                                    self._runner(srv))
        self.assertEqual(code, 0)
        combined = out + err
        self.assertIn("no longer", combined.lower())
        self.assertNotIn("Traceback", combined)


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name,
        )

    # Rule 9
    def test_429_exits_with_the_rate_limit_hint_not_a_traceback(self):
        with FakeInteractionsServer() as srv:
            srv.reply_status = 429
            srv.reply_body = {"error": {
                "code": 429, "status": "RESOURCE_EXHAUSTED",
                "message": "Resource has exhausted the free tier quota"}}
            code, out, err = invoke(
                ["ask", "summarise this"], self._runner(srv))
        combined = out + err
        self.assertNotEqual(code, 0)
        self.assertNotIn("Traceback", combined)
        self.assertIn("rate limit", combined.lower())
        # The reason we never retry, stated where the user can read it.
        self.assertIn("idempotent", combined.lower())
        self.assertIn("429", combined)

    # Rule 10
    def test_other_errors_keep_their_message_and_get_no_hint(self):
        with FakeInteractionsServer() as srv:
            srv.reply_status = 500
            srv.reply_body = {"error": {"code": 500,
                                        "message": "backend exploded"}}
            code, out, err = invoke(
                ["ask", "summarise this"], self._runner(srv))
        combined = out + err
        self.assertNotEqual(code, 0)
        self.assertNotIn("Traceback", combined)
        self.assertIn("backend exploded", combined)
        self.assertNotIn("rate limit", combined.lower())


if __name__ == "__main__":
    unittest.main()
