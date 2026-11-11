"""D-1 (T7): a local function call executes at most once per
(interaction, call), even when the process dies in the window between the
side effect and the round that reports it to Google.

The gap: run_until_complete dispatches the call locally (memory_save
persists to Engram), THEN creates the interaction that carries the result.
A process killed in between leaves the record pointing at the
requires_action interaction; continue re-reads it, the same call id is
pending again, and the side effect ran twice.

The fix persists each answered call in the task store under
(interaction_id, call_id) right after the side effect, and the loop
replays the stored answer instead of re-executing.

Rules under test:

1. A resumed round replays the stored answer: the dispatcher (fresh
   process, fresh instance) is not called again, and the API still
   receives the function_result with the ORIGINAL answer.
2. The key is (interaction_id, call_id): the same call id under a
   DIFFERENT interaction is a new call and executes again.
3. A failed dispatch is not cached: the retry executes the call again.
4. With no cache the loop behaves as before (regression guard): every
   pending call is dispatched.
"""

import tempfile
import unittest

from remote_worker.function_loop import run_until_complete
from remote_worker.state import TaskStore


class CountingDispatcher:
    """A local tool whose dispatch has an observable side effect."""

    def __init__(self, answer='{"ok": true}'):
        self.calls = []
        self.answer = answer

    def tool_specs(self):
        return [{"name": "memory_save"}]

    def dispatch(self, call):
        self.calls.append(call.get("id"))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _interaction(iid, status, call_id=None):
    steps = []
    if call_id:
        steps.append({"type": "function_call", "id": call_id,
                      "name": "memory_save",
                      "args": {"title": "t", "content": "c"}})
    return {"id": iid, "status": status, "steps": steps,
            "agent": "a", "environment_id": "env_x"}


class FakeClient:
    """Serves a scripted sequence of interactions per get/create."""

    def __init__(self, reads):
        # reads: list of interaction dicts returned by get_interaction,
        # one per call.
        self._reads = list(reads)
        self.created = []  # (previous_interaction_id, input)

    def get_interaction(self, iid):
        return self._reads.pop(0)

    def create_interaction(self, *, agent, input, environment,
                           previous_interaction_id, system_instruction=None,
                           tools=None):
        self.created.append((previous_interaction_id, input))
        # The continuation carries the answers; it is terminal.
        return {"id": "next-" + (previous_interaction_id or "?"),
                "status": "completed", "steps": [], "agent": agent,
                "environment_id": environment}


class IdempotentCallsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)
        self.store.record(create=True, task={
            "task_id": "t1", "status": "in_progress",
            "interaction_id": "i1"})

    def _cache(self):
        from remote_worker.runner import _LocalCallCache
        return _LocalCallCache(self.store, "t1")

    # Rule 1: the crash window.
    def test_a_resumed_round_replays_and_does_not_reexecute(self):
        first = CountingDispatcher()
        client = FakeClient([
            _interaction("i1", "requires_action", call_id="call_1"),
        ])
        run_until_complete(client, first,
                           _interaction("i1", "requires_action",
                                        call_id="call_1"),
                           call_cache=self._cache())
        self.assertEqual(first.calls, ["call_1"])

        # The process died after the side effect, before (or during) the
        # round that reports it: a fresh process re-reads the SAME
        # interaction, with the same call pending.
        again = CountingDispatcher()
        client2 = FakeClient([
            _interaction("i1", "requires_action", call_id="call_1"),
        ])
        final = run_until_complete(
            client2, again,
            _interaction("i1", "requires_action", call_id="call_1"),
            call_cache=self._cache())
        # The side effect ran ONCE in total, across both processes.
        self.assertEqual(again.calls, [], "the call ran twice")
        # The API still received the result, with the original answer.
        self.assertEqual(final["status"], "completed")
        sent = client2.created[0][1]
        self.assertEqual(sent[0]["name"], "memory_save")
        self.assertEqual(sent[0]["call_id"], "call_1")
        self.assertEqual(sent[0]["result"], '{"ok": true}')

    # Rule 2: the interaction id is part of the key.
    def test_the_same_call_id_in_a_new_interaction_executes(self):
        first = CountingDispatcher()
        run_until_complete(
            FakeClient([_interaction("i1", "requires_action",
                                     call_id="call_1")]),
            first,
            _interaction("i1", "requires_action", call_id="call_1"),
            call_cache=self._cache())
        second = CountingDispatcher()
        run_until_complete(
            FakeClient([_interaction("i2", "requires_action",
                                     call_id="call_1")]),
            second,
            _interaction("i2", "requires_action", call_id="call_1"),
            call_cache=self._cache())
        self.assertEqual(first.calls, ["call_1"])
        self.assertEqual(second.calls, ["call_1"],
                         "a different interaction must not be replayed")

    # Rule 3: failures are not cached.
    def test_a_failed_dispatch_is_retried(self):
        boom = CountingDispatcher(answer=ValueError("boom"))
        with self.assertRaises(ValueError):
            run_until_complete(
                FakeClient([_interaction("i1", "requires_action",
                                         call_id="call_1")]),
                boom,
                _interaction("i1", "requires_action", call_id="call_1"),
                call_cache=self._cache())
        # Nothing was persisted for the failed call.
        self.assertIsNone(self._cache().get("i1", "call_1"))
        retry = CountingDispatcher()
        run_until_complete(
            FakeClient([_interaction("i1", "requires_action",
                                     call_id="call_1")]),
            retry,
            _interaction("i1", "requires_action", call_id="call_1"),
            call_cache=self._cache())
        self.assertEqual(retry.calls, ["call_1"],
                         "a failed call must be re-executed")

    # Rule 4 (regression guard).
    def test_without_a_cache_every_call_is_dispatched(self):
        d1 = CountingDispatcher()
        run_until_complete(
            FakeClient([_interaction("i1", "requires_action",
                                     call_id="call_1")]),
            d1, _interaction("i1", "requires_action", call_id="call_1"))
        d2 = CountingDispatcher()
        run_until_complete(
            FakeClient([_interaction("i1", "requires_action",
                                     call_id="call_1")]),
            d2, _interaction("i1", "requires_action", call_id="call_1"))
        self.assertEqual(d1.calls, ["call_1"])
        self.assertEqual(d2.calls, ["call_1"])


if __name__ == "__main__":
    unittest.main()
