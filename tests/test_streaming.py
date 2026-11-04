"""Tests for streaming SSE (TODO #50).

Rules under test:
1. stream_interaction requests `alt=sse` and parses the SSE event stream.
2. It yields text deltas to an on_delta callback as they arrive.
3. It returns the final interaction from `interaction.completed`, with usage.
4. A non-streaming request still works unchanged (no alt=sse).
"""

import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.google_client import InteractionsClient
except ImportError:
    InteractionsClient = None


def _sse_stream(final_interaction, text_chunks):
    events = [
        {"event_type": "interaction.created",
         "interaction": {"id": "v1_sse", "status": "in_progress"}},
        {"event_type": "step.start", "index": 0, "step": {"type": "thought"}},
        {"event_type": "step.delta", "index": 0,
         "delta": {"type": "thought_signature", "signature": "..."}},
        {"event_type": "step.stop", "index": 0},
        {"event_type": "step.start", "index": 1, "step": {"type": "model_output"}},
    ]
    for chunk in text_chunks:
        events.append({"event_type": "step.delta", "index": 1,
                       "delta": {"type": "text", "text": chunk}})
    events.append({"event_type": "step.stop", "index": 1,
                   "usage": {"total_tokens": 123},
                   "step_usage": {"total_tokens": 45}})
    events.append({"event_type": "interaction.completed",
                   "interaction": final_interaction})
    return events


class TestStreaming(unittest.TestCase):
    def setUp(self):
        if InteractionsClient is None:
            self.fail("RED: InteractionsClient.stream_interaction not implemented")

    def test_stream_requests_sse_and_parses(self):
        final = {"id": "v1_sse", "status": "completed",
                 "usage": {"total_tokens": 123}}
        with FakeInteractionsServer() as srv:
            srv.sse_events = _sse_stream(final, ["Hello", " world"])
            client = InteractionsClient(api_key="AQ.k", base_url=srv.base_url)
            deltas = []
            result = client.stream_interaction(
                agent="antigravity-preview-09-2026", input="hi",
                environment="remote", on_delta=deltas.append,
            )
            path = srv.last_request["path"]
            body = srv.last_request["body"]
        self.assertIn("alt=sse", path)
        self.assertIn('"stream": true', body)
        self.assertEqual("".join(deltas), "Hello world")
        self.assertEqual(result["id"], "v1_sse")
        self.assertEqual(result["status"], "completed")

    def test_delta_callback_gets_text_in_order(self):
        final = {"id": "v1_sse", "status": "completed", "usage": {}}
        with FakeInteractionsServer() as srv:
            srv.sse_events = _sse_stream(final, ["a", "b", "c"])
            client = InteractionsClient(api_key="AQ.k", base_url=srv.base_url)
            deltas = []
            client.stream_interaction(agent="ag", input="x",
                                      environment="remote",
                                      on_delta=deltas.append)
        self.assertEqual(deltas, ["a", "b", "c"])

    def test_returns_final_interaction(self):
        final = {"id": "v1_sse", "status": "completed",
                 "usage": {"total_tokens": 123}}
        with FakeInteractionsServer() as srv:
            srv.sse_events = _sse_stream(final, ["ok"])
            client = InteractionsClient(api_key="AQ.k", base_url=srv.base_url)
            result = client.stream_interaction(agent="ag", input="x",
                                               environment="remote")
        self.assertEqual(result["usage"]["total_tokens"], 123)

    def test_non_streaming_still_works(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = {"id": "v1_x", "status": "completed",
                              "steps": [{"type": "model_output",
                                         "content": [{"text": "plain"}]}],
                              "usage": {}}
            client = InteractionsClient(api_key="AQ.k", base_url=srv.base_url)
            result = client.create_interaction(agent="ag", input="x",
                                               environment="remote")
            self.assertNotIn("alt=sse", srv.last_request["path"])
            self.assertEqual(result["id"], "v1_x")


if __name__ == "__main__":
    unittest.main()
