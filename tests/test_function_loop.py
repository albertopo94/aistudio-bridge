"""WU-2: tests for remote_worker.function_loop.

Rules under test:
1. A completed interaction is returned untouched, with no dispatch.
2. A pending function call is dispatched locally and answered with a
   ``function_result`` that carries previous_interaction_id, environment
   and agent (the continuity the PoC required).
3. A call that already has a matching ``function_result`` is not executed
   again (the API echoes past steps back).
4. An endless ``requires_action`` is bounded by max_steps.
5. ``requires_action`` with no pending call is an error, not a silent loop.
6. The continuation re-sends the interaction-scoped ``system_instruction``
   and ``tools`` when the caller provides them (G15): without them the
   resumed turn loses the worker contract and the memory tools. When the
   caller does not provide them, they are omitted.
"""

import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.function_loop import (
        LoopLimitExceeded,
        NoPendingCallsError,
        pending_calls,
        run_until_complete,
    )
except ImportError:
    run_until_complete = None


def interaction(status, steps=None, iid="v1_i", env="env_1", agent="ag"):
    return {
        "id": iid,
        "status": status,
        "environment_id": env,
        "agent": agent,
        "steps": steps or [],
        "usage": {},
    }


def call_step(cid="call_1", name="memory_search", args=None):
    return {
        "type": "function_call",
        "name": name,
        "id": cid,
        "arguments": args if args is not None else {"query": "marcador"},
    }


class SpyDispatcher:
    def __init__(self, result='{"ok": true, "content": "SOL-VERDE-77"}'):
        self.calls = []
        self.result = result

    def dispatch(self, call):
        self.calls.append(call)
        return self.result


class TestPendingCalls(unittest.TestCase):
    def setUp(self):
        if run_until_complete is None:
            self.fail("RED: remote_worker.function_loop not implemented yet")

    # Rule 3: answered calls are not pending.
    def test_answered_call_is_not_pending(self):
        steps = [
            call_step("call_1"),
            {"type": "function_result", "call_id": "call_1",
             "name": "memory_search", "result": "{}"},
        ]
        self.assertEqual(pending_calls({"steps": steps}), [])

    def test_unanswered_call_is_pending(self):
        steps = [call_step("call_2")]
        self.assertEqual(len(pending_calls({"steps": steps})), 1)


class TestRunUntilComplete(unittest.TestCase):
    def setUp(self):
        if run_until_complete is None:
            self.fail("RED: remote_worker.function_loop not implemented yet")

    def _client(self, srv):
        from remote_worker.google_client import InteractionsClient
        return InteractionsClient(api_key="AQ.fake", base_url=srv.base_url)

    # Rule 1: already completed -> returned as-is, nothing dispatched.
    def test_completed_returns_immediately(self):
        dispatcher = SpyDispatcher()
        with FakeInteractionsServer() as srv:
            client = self._client(srv)
            result = run_until_complete(
                client, dispatcher, interaction("completed")
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(dispatcher.calls, [])
        self.assertEqual(srv.requests, [])

    # Rule 2: one pending call -> dispatch + function_result continuation.
    def test_dispatch_and_continue(self):
        dispatcher = SpyDispatcher()
        first = interaction("requires_action", [call_step("call_1")])
        with FakeInteractionsServer() as srv:
            srv.reply_body = interaction("completed")
            client = self._client(srv)
            result = run_until_complete(client, dispatcher, first)
            body = srv.last_body
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(dispatcher.calls), 1)

        # continuity is carried explicitly
        self.assertEqual(body["previous_interaction_id"], "v1_i")
        self.assertEqual(body["environment"], "env_1")
        self.assertEqual(body["agent"], "ag")
        # the answer is a function_result for the exact call id
        self.assertEqual(len(body["input"]), 1)
        answer = body["input"][0]
        self.assertEqual(answer["type"], "function_result")
        self.assertEqual(answer["call_id"], "call_1")
        self.assertEqual(answer["name"], "memory_search")
        self.assertIsInstance(answer["result"], str)

    # Rule 4: the loop is bounded.
    def test_loop_limit_is_enforced(self):
        dispatcher = SpyDispatcher()
        first = interaction("requires_action", [call_step("call_1")])
        with FakeInteractionsServer() as srv:
            srv.reply_body = interaction(
                "requires_action", [call_step("call_2")]
            )
            client = self._client(srv)
            with self.assertRaises(LoopLimitExceeded):
                run_until_complete(
                    client, dispatcher, first, max_steps=2
                )

    # Rule 6: continuation carries the interaction-scoped parameters (G15).
    def test_continuation_resends_scoped_params_when_given(self):
        dispatcher = SpyDispatcher()
        first = interaction("requires_action", [call_step("call_1")])
        tools = [{"type": "code_execution"}, {"type": "function",
                 "name": "memory_search"}]
        with FakeInteractionsServer() as srv:
            srv.reply_body = interaction("completed")
            client = self._client(srv)
            run_until_complete(
                client, dispatcher, first,
                system_instruction="WORKER CONTRACT",
                tools=tools,
            )
            body = srv.last_body
        self.assertEqual(body["system_instruction"], "WORKER CONTRACT")
        self.assertEqual(body["tools"], tools)

    def test_continuation_omits_scoped_params_when_not_given(self):
        dispatcher = SpyDispatcher()
        first = interaction("requires_action", [call_step("call_1")])
        with FakeInteractionsServer() as srv:
            srv.reply_body = interaction("completed")
            client = self._client(srv)
            run_until_complete(client, dispatcher, first)
            body = srv.last_body
        self.assertNotIn("system_instruction", body)
        self.assertNotIn("tools", body)

    # Rule 5: requires_action with nothing to do is an error.
    def test_requires_action_without_pending_call_raises(self):
        dispatcher = SpyDispatcher()
        with FakeInteractionsServer() as srv:
            client = self._client(srv)
            with self.assertRaises(NoPendingCallsError):
                run_until_complete(
                    client, dispatcher, interaction("requires_action", [])
                )


if __name__ == "__main__":
    unittest.main()