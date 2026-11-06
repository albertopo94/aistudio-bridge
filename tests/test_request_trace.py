"""REMOTE_WORKER_TRACE: an on-disk record of what the client actually sent.

Why this exists: the worker contract must travel on EVERY interaction (G15).
Unit tests prove it against the local fake server; proving it against the real
API needs evidence the human can read afterwards. Without a recorded request
there is nothing to inspect — only an inference from the worker's behaviour.

Rules under test:
1. No ``REMOTE_WORKER_TRACE`` in the environment means no file is written and
   no behaviour changes (the trace is opt-in diagnostics, never default).
2. When it is set, each request appends one JSON line recording the method, the
   URL and the request body — including ``system_instruction``, ``tools`` and
   ``previous_interaction_id``, the fields G15 is about.
3. One line per request, in call order: the continuation turn is visible as its
   own line, which is exactly what the live smoke has to show.
4. The trace never contains a credential: the key travels in a header that is
   not recorded, and a secret-shaped field inside a body is masked.
5. A trace that cannot be written never breaks the real request.
"""

import json
import os
import tempfile
import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.google_client import InteractionsClient
except ImportError:  # pragma: no cover
    InteractionsClient = None

TRACE_VAR = "REMOTE_WORKER_TRACE"


@unittest.skipIf(InteractionsClient is None, "remote_worker not implemented")
class RequestTraceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace = os.path.join(self._tmp.name, "trace.jsonl")
        self._saved = os.environ.pop(TRACE_VAR, None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop(TRACE_VAR, None)
        if self._saved is not None:
            os.environ[TRACE_VAR] = self._saved

    def _client(self, base_url):
        return InteractionsClient(api_key="SECRET-KEY-123", base_url=base_url)

    def _lines(self):
        with open(self.trace, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_no_trace_var_means_no_file_and_no_change(self):
        with FakeInteractionsServer() as srv:
            interaction = self._client(srv.base_url).create_interaction(
                agent="antigravity-preview-09-2026", input="hola"
            )
        self.assertEqual(interaction["status"], "completed")
        self.assertFalse(os.path.exists(self.trace))
        self.assertEqual(len(srv.requests), 1)

    def test_trace_records_the_contract_and_the_tools(self):
        os.environ[TRACE_VAR] = self.trace
        with FakeInteractionsServer() as srv:
            self._client(srv.base_url).create_interaction(
                agent="antigravity-preview-09-2026", input="hola",
                system_instruction="You are a delegated code worker",
                tools=[{"type": "code_execution"}],
            )
        body = self._lines()[0]["body"]
        self.assertEqual(body["system_instruction"],
                         "You are a delegated code worker")
        self.assertEqual(body["tools"], [{"type": "code_execution"}])

    def test_each_request_appends_one_line_in_order(self):
        """The shape the live smoke needs: the continuation as its own line."""
        os.environ[TRACE_VAR] = self.trace
        client = None
        with FakeInteractionsServer() as srv:
            client = self._client(srv.base_url)
            client.create_interaction(
                agent="antigravity-preview-09-2026", input="tarea",
                system_instruction="CONTRACT", tools=[{"type": "code_execution"}],
            )
            client.create_interaction(
                agent="antigravity-preview-09-2026",
                input=[{"type": "function_result", "call_id": "c1",
                        "result": "ok"}],
                previous_interaction_id="v1_test",
                system_instruction="CONTRACT", tools=[{"type": "code_execution"}],
            )
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(srv.requests), 2)
        for line in lines:
            self.assertEqual(line["method"], "POST")
            self.assertEqual(line["body"]["system_instruction"], "CONTRACT")
        self.assertNotIn("previous_interaction_id", lines[0]["body"])
        self.assertEqual(lines[1]["body"]["previous_interaction_id"], "v1_test")

    def test_trace_never_records_a_credential(self):
        os.environ[TRACE_VAR] = self.trace
        with FakeInteractionsServer() as srv:
            self._client(srv.base_url).create_interaction(
                agent="antigravity-preview-09-2026",
                input=[{"type": "function_result", "call_id": "c1",
                        "result": {"apiKey": "leak-me",
                                   "nested": {"x-goog-api-key": "leak-me-too"}}}],
            )
        with open(self.trace, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn("SECRET-KEY-123", raw)
        self.assertNotIn("leak-me", raw)
        self.assertNotIn("leak-me-too", raw)
        self.assertIn("leak-me", srv.last_request["body"])
        self.assertIn("leak-me-too", srv.last_request["body"])

    def test_unwritable_trace_never_breaks_the_request(self):
        blocker = os.path.join(self._tmp.name, "not-a-dir")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.environ[TRACE_VAR] = os.path.join(blocker, "trace.jsonl")
        with FakeInteractionsServer() as srv:
            interaction = self._client(srv.base_url).create_interaction(
                agent="antigravity-preview-09-2026", input="hola",
                system_instruction="CONTRACT",
            )
        self.assertEqual(interaction["status"], "completed")
        self.assertFalse(os.path.exists(os.path.join(blocker, "trace.jsonl")))

    def test_streaming_request_is_traced_too(self):
        os.environ[TRACE_VAR] = self.trace
        with FakeInteractionsServer() as srv:
            srv.sse_events = [
                {"event_type": "interaction.completed",
                 "interaction": {"id": "v1", "status": "completed",
                                 "steps": [], "usage": {}}},
            ]
            self._client(srv.base_url).stream_interaction(
                agent="antigravity-preview-09-2026", input="hola",
                system_instruction="CONTRACT", tools=[{"type": "google_search"}],
            )
        body = self._lines()[0]["body"]
        self.assertEqual(body["system_instruction"], "CONTRACT")
        self.assertEqual(body["tools"], [{"type": "google_search"}])


if __name__ == "__main__":
    unittest.main()
