"""WU-1: tests for remote_worker.google_client.InteractionsClient.

Each test asserts exactly one rule from the plan (03-plan-paso-a-paso.md,
WU-1): the exact payload sent, model_output parsing, 429 handling, and
timeout handling.
"""

import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.google_client import InteractionsClient
except ImportError:
    InteractionsClient = None  # RED state: module not implemented yet


def completed_interaction(steps=None, extra=None):
    body = {
        "id": "v1_test_interaction",
        "status": "completed",
        "environment_id": "env_test",
        "usage": {
            "total_tokens": 100,
            "total_input_tokens": 80,
            "total_output_tokens": 20,
        },
        "steps": steps if steps is not None else [],
    }
    if extra:
        body.update(extra)
    return body


class TestInteractionsClient(unittest.TestCase):
    def setUp(self):
        if InteractionsClient is None:
            self.fail("RED: remote_worker.google_client not implemented yet")

    # Rule 1: exact payload — key only in the x-goog-api-key header, body as
    # declared, correct path.
    def test_create_sends_key_only_in_header_and_exact_body(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = completed_interaction()
            client = InteractionsClient(
                api_key="AQ.fake-test-key", base_url=srv.base_url
            )
            result = client.create_interaction(
                agent="antigravity-preview-09-2026",
                input="hello",
                environment="remote",
            )
            req = srv.last_request
            self.assertEqual(req["path"], "/v1beta/interactions")
            self.assertEqual(req["headers"]["x-goog-api-key"], "AQ.fake-test-key")
            self.assertEqual(
                req["body"],
                '{"agent": "antigravity-preview-09-2026", "input": "hello", '
                '"environment": "remote"}',
            )
            # the key must not leak into the body
            self.assertNotIn("AQ.fake-test-key", req["body"])
            self.assertEqual(result["id"], "v1_test_interaction")

    # Rule 2: the answer text is the LAST model_output step, not output_text.
    def test_last_model_output_reads_last_step_not_output_text(self):
        steps = [
            {"type": "model_output",
             "content": [{"text": "first answer"}]},
            {"type": "code_execution_call", "arguments": {}},
            {"type": "model_output",
             "content": [{"text": "final answer"}]},
        ]
        with FakeInteractionsServer() as srv:
            srv.reply_body = completed_interaction(
                steps=steps, extra={"output_text": "WRONG - not the answer"}
            )
            client = InteractionsClient(api_key="AQ.fake", base_url=srv.base_url)
            interaction = client.create_interaction(
                agent="antigravity-preview-09-2026", input="x", environment="remote"
            )
            self.assertEqual(client.last_model_output(interaction), "final answer")

    # Rule 3: a 429 is reported to the caller with the body; no automatic
    # retry (exactly one request is recorded).
    def test_rate_limit_is_reported_without_retry(self):
        with FakeInteractionsServer() as srv:
            srv.reply_status = 429
            srv.reply_body = {
                "error": {
                    "message": "resource exhausted",
                    "code": "resourceExhausted",
                }
            }
            client = InteractionsClient(api_key="AQ.fake", base_url=srv.base_url)
            with self.assertRaises(Exception) as ctx:
                client.create_interaction(
                    agent="antigravity-preview-09-2026", input="x",
                    environment="remote",
                )
            self.assertEqual(len(srv.requests), 1)  # no retry
            self.assertIn("429", str(ctx.exception))
            self.assertIn("resource exhausted", str(ctx.exception))

    # Rule 4: a hung server produces a clear timeout error, not a hang.
    def test_timeout_raises_clear_error(self):
        with FakeInteractionsServer() as srv:
            srv.reply_delay = 5.0  # longer than the client's timeout
            client = InteractionsClient(
                api_key="AQ.fake", base_url=srv.base_url, timeout=0.5
            )
            with self.assertRaises(Exception) as ctx:
                client.create_interaction(
                    agent="antigravity-preview-09-2026", input="x",
                    environment="remote",
                )
            self.assertIn("timeout", str(ctx.exception).lower())

    # Rule 5: usage() exposes the token counts from the interaction.
    def test_usage_returns_token_counts(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = completed_interaction()
            client = InteractionsClient(api_key="AQ.fake", base_url=srv.base_url)
            interaction = client.create_interaction(
                agent="antigravity-preview-09-2026", input="x", environment="remote"
            )
            usage = client.usage(interaction)
            self.assertEqual(usage["total_tokens"], 100)
            self.assertEqual(usage["total_input_tokens"], 80)
            self.assertEqual(usage["total_output_tokens"], 20)


if __name__ == "__main__":
    unittest.main()
