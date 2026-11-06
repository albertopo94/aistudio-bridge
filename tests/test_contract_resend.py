"""G15+G22: the worker contract and tools must survive every continuation.

The Interactions API treats ``system_instruction``, ``tools`` and
``agent_config`` as interaction-scoped: each new interaction must re-send
them or the resumed turn runs without the worker contract (the anti-push /
read-AGENTS.md rule) and without the memory tools.

Rules under test:
1. The initial call of ``run`` sends the contract and the tools (invariant
   pinned so the fix cannot regress by dropping the initial send).
2. The ``function_result`` continuation of ``run`` re-sends the same
   contract (with the repo dir) and the same tools.
3. The continuation of ``ask`` re-sends the ask contract and ask tools.
4. ``continue_task`` rebuilds the contract from the task record and re-sends
   contract + tools on the continuation call.
5. A record without ``repo_dir`` (created before this fix) still continues:
   the contract falls back to "(no repository mounted)".
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


def _interaction(status, steps=None, iid="v1", env="env_1", tokens=42):
    return {
        "id": iid,
        "status": status,
        "environment_id": env,
        "agent": "antigravity-preview-09-2026",
        "usage": {"total_tokens": tokens},
        "steps": steps or [],
    }


def _call(cid="call_1", name="memory_search"):
    return {"type": "function_call", "name": name, "id": cid,
            "arguments": {"query": "marcador"}}


def _result_step(cid="call_1", name="memory_search"):
    return {"type": "function_result", "call_id": cid, "name": name,
            "result": '{"ok": true}'}


class StubMemory:
    def tool_specs(self):
        return [{"type": "function", "name": "memory_search",
                 "parameters": {"type": "object", "properties": {}}}]

    def dispatch(self, call):
        return '{"ok": true, "content": "stub"}'


class TestContractResend(unittest.TestCase):
    def setUp(self):
        if TaskRunner is None:
            self.fail("RED: remote_worker.runner not implemented yet")
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(project="proj", root=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=None,
            dispatcher=StubMemory(),
            store=self.store,
            workdir=self._tmp.name,
        )

    # Rule 1+2: run's continuation must carry contract + tools.
    def test_run_continuation_carries_contract_and_tools(self):
        with FakeInteractionsServer() as srv:
            srv.replies = [
                (200, _interaction("requires_action", [_call()], iid="v1")),
                (200, _interaction("incomplete", iid="v2")),
            ]
            runner = self._runner(srv)
            result = runner.run(
                task="do the thing",
                repo_url="https://github.com/o/repo-x",
            )
            initial = json.loads(srv.requests[0]["body"])
            continuation = json.loads(srv.requests[1]["body"])
        self.assertEqual(result["status"], "incomplete")
        # The record must carry repo_dir so continue can rebuild the contract.
        record = self.store.get(result["task_id"])
        self.assertEqual(record["repo_dir"], "/workspace/repo-x")
        self.assertEqual(record["kind"], "run")
        init_body = initial
        # Rule 1 (invariant): initial carries both.
        self.assertIn("AGENTS.md", init_body["system_instruction"])
        self.assertIn("/workspace/repo-x", init_body["system_instruction"])
        self.assertIn({"type": "code_execution"}, init_body["tools"])
        self.assertEqual(
            [t.get("name") for t in init_body["tools"] if "name" in t],
            ["memory_search"],
        )
        # Rule 2: continuation is a function_result AND carries both.
        cont_body = continuation
        self.assertEqual(cont_body["input"][0]["type"], "function_result")
        self.assertIn("AGENTS.md", cont_body["system_instruction"])
        self.assertIn("/workspace/repo-x", cont_body["system_instruction"])
        self.assertIn({"type": "code_execution"}, cont_body["tools"])
        self.assertIn({"type": "function", "name": "memory_search",
                       "parameters": {"type": "object",
                                      "properties": {}}}, cont_body["tools"])

    # Rule 3: ask's continuation carries the ask contract and ask tools.
    def test_ask_continuation_carries_contract_and_tools(self):
        with FakeInteractionsServer() as srv:
            srv.replies = [
                (200, _interaction("requires_action", [_call()], iid="a1")),
                (200, _interaction("completed", iid="a2")),
            ]
            runner = self._runner(srv)
            result = runner.ask(task="research something")
            continuation = json.loads(srv.requests[1]["body"])
        self.assertEqual(result["status"], "completed")
        cont_body = continuation
        self.assertEqual(cont_body["input"][0]["type"], "function_result")
        self.assertIn("AGENTS.md", cont_body["system_instruction"])
        self.assertIn({"type": "code_execution"}, cont_body["tools"])
        self.assertIn({"type": "google_search"}, cont_body["tools"])
        self.assertIn({"type": "url_context"}, cont_body["tools"])

    # Rule 4: continue_task rebuilds contract+tools from the record.
    def test_continue_task_sends_contract_and_tools(self):
        self.store.record({
            "task_id": "t_old", "kind": "run", "status": "incomplete",
            "interaction_id": "v9", "environment_id": "env_9",
            "repo_url": "https://github.com/o/repo-x",
            "repo_dir": "/workspace/repo-x",
            "model": "gemini-3.8-flash", "budget": 150000,
            "task": "earlier task",
        })
        with FakeInteractionsServer() as srv:
            # The pre-check read says the conversation is over, then the
            # continuation is sent and completes.
            srv.get_replies.append((200, json.dumps(
                _interaction("completed", iid="v9")).encode()))
            srv.reply_body = _interaction("completed", iid="v10")
            runner = self._runner(srv)
            result = runner.continue_task(task_id="t_old")
            body = srv.last_body
        self.assertEqual(result["status"], "completed")
        self.assertEqual(body["previous_interaction_id"], "v9")
        self.assertIn("AGENTS.md", body["system_instruction"])
        self.assertIn("/workspace/repo-x", body["system_instruction"])
        self.assertIn({"type": "code_execution"}, body["tools"])

    # Rule 5: a pre-fix record without repo_dir still continues.
    def test_continue_task_without_repo_dir_falls_back(self):
        self.store.record({
            "task_id": "t_legacy", "status": "incomplete",
            "interaction_id": "v9", "environment_id": "env_9",
            "repo_url": "https://github.com/o/repo-x",
            "model": "gemini-3.8-flash", "budget": 150000,
            "task": "earlier task",
        })
        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                _interaction("completed", iid="v9")).encode()))
            srv.reply_body = _interaction("completed", iid="v10")
            runner = self._runner(srv)
            result = runner.continue_task(task_id="t_legacy")
            body = srv.last_body
        self.assertEqual(result["status"], "completed")
        self.assertIn("AGENTS.md", body["system_instruction"])


if __name__ == "__main__":
    unittest.main()
