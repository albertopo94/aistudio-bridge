"""Tests for the `ask` mode: delegate a non-code task and get the text.

Rules under test:
1. ask does NOT mount a repository and does NOT download a workspace: it is
   a pure text round trip.
2. ask still resolves memory callbacks through the local Engram store.
3. ask returns the worker's text (last_model_output) plus the token usage.
4. ask honours the budget and the model floor through the planner.
5. ask records the task in the state store so it can be inspected.
"""

import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.runner import TaskRunner
except ImportError:
    TaskRunner = None


def _completed(steps, iid="v1_ask", env="env_ask", tokens=42):
    return {
        "id": iid,
        "status": "completed",
        "environment_id": env,
        "agent": "antigravity-preview-09-2026",
        "usage": {"total_tokens": tokens},
        "steps": steps,
    }


class StubMemory:
    def __init__(self):
        self.calls = []

    def tool_specs(self):
        return [{"type": "function", "name": "memory_search",
                 "parameters": {"type": "object", "properties": {}}}]

    def dispatch(self, call):
        self.calls.append(call)
        return '{"ok": true, "content": "stub"}'


class TestAskMode(unittest.TestCase):
    def setUp(self):
        if TaskRunner is None or not hasattr(TaskRunner, "ask"):
            self.fail("RED: TaskRunner.ask not implemented yet")

    def _runner(self, srv, store=None):
        from remote_worker.google_client import InteractionsClient
        from remote_worker.state import TaskStore

        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=None,  # ask must never touch the FilesClient
            dispatcher=StubMemory(),
            store=store or TaskStore(project="proj"),
            workdir="/tmp/work",
        )

    # Rule 1: no sources (no repository) and no download.
    def test_ask_mounts_no_repository(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _completed([{
                "type": "model_output", "content": [{"text": "the answer"}],
            }])
            runner = self._runner(srv)
            result = runner.ask("Summarize this topic")
            body = srv.last_body
        # the environment is the bare string "remote", not a config object
        self.assertEqual(body["environment"], "remote")
        self.assertNotIn("sources", body.get("environment", {}))
        # and no download happened: the result is pure text
        self.assertEqual(result["text"], "the answer")
        self.assertEqual(result["tokens"], 42)

    # Rule 2: memory tools are still attached and answered.
    def test_ask_resolves_memory_callbacks(self):
        steps = [
            {"type": "function_call", "name": "memory_search",
             "id": "call_1", "arguments": {"query": "x"}},
            {"type": "model_output", "content": [{"text": "done"}]},
        ]
        with FakeInteractionsServer() as srv:
            srv.reply_body = _completed(steps)
            runner = self._runner(srv)
            result = runner.ask("Use memory")
            tools = srv.last_body["tools"]
        self.assertIn(
            {"type": "function", "name": "memory_search",
             "parameters": {"type": "object", "properties": {}}},
            tools,
        )
        self.assertEqual(result["text"], "done")

    # Rule 3: the text comes from the last model_output, not a diff.
    def test_ask_returns_text_not_diff(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _completed([{
                "type": "model_output",
                "content": [{"text": "research conclusion"}],
            }])
            runner = self._runner(srv)
            result = runner.ask("Research something")
        self.assertNotIn("clone_dir", result)
        self.assertNotIn("diff", result)
        self.assertEqual(result["text"], "research conclusion")
        self.assertEqual(result["status"], "completed")

    # Rule 4: model floor and budget flow through the planner.
    def test_ask_rejects_model_below_floor(self):
        from remote_worker.planner import UnsupportedModelError

        with FakeInteractionsServer() as srv:
            runner = self._runner(srv)
            with self.assertRaises(UnsupportedModelError):
                runner.ask("Task", model="gemini-2.5-flash")

    def test_ask_honours_budget(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _completed([])
            runner = self._runner(srv)
            runner.ask("Task", budget=7777)
            self.assertEqual(srv.last_body["agent_config"]["max_total_tokens"],
                             7777)

    # Rule 5: ask records the task in the state store.
    def test_ask_records_task_in_store(self):
        import tempfile

        from remote_worker.state import TaskStore

        with tempfile.TemporaryDirectory() as tmp, FakeInteractionsServer() as srv:
            store = TaskStore(project="proj", root=tmp)
            srv.reply_body = _completed([{
                "type": "model_output", "content": [{"text": "the answer"}],
            }])
            runner = self._runner(srv, store=store)
            result = runner.ask("Summarize this")
            task = store.get(result["task_id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["kind"], "ask")


if __name__ == "__main__":
    unittest.main()