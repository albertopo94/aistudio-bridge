"""WU-4a: tests for remote_worker.planner and remote_worker.state.

Planner rules:
1. Only the two models the user chose are accepted; anything lower is
   rejected rather than silently downgraded.
2. The budget is always explicit in ``agent_config`` and must be positive.
3. The worker contract states the delegation role, the memory rule and the
   report format — the three things the PoC showed the worker needed.

State rules:
4. The state file is private (0600) and survives a reload.
5. An unknown task id is an error, not an empty answer.
"""

import json
import os
import stat
import tempfile
import unittest

try:
    from remote_worker.planner import (
        ALLOWED_MODELS,
        DEFAULT_BUDGET,
        DEFAULT_MODEL,
        UnsupportedModelError,
        agent_config,
        worker_contract,
    )
    from remote_worker.state import TaskStore
except ImportError:
    agent_config = None


class TestPlanner(unittest.TestCase):
    def setUp(self):
        if agent_config is None:
            self.fail("RED: remote_worker.planner not implemented yet")

    # Rule 1: the allowed set is exactly Argon and 3.8 Flash.
    def test_allowed_models(self):
        self.assertEqual(
            set(ALLOWED_MODELS), {"gemini-4-argon", "gemini-3.8-flash"}
        )

    # Rule 1: a lower model is rejected, never downgraded silently.
    def test_rejects_model_below_the_floor(self):
        with self.assertRaises(UnsupportedModelError) as ctx:
            agent_config(model="gemini-3.5-flash-lite")
        self.assertIn("gemini-3.5-flash-lite", str(ctx.exception))

    # Rule 2: default model and budget produce an exact agent_config.
    def test_default_agent_config(self):
        self.assertEqual(agent_config(), {
            "type": "antigravity",
            "model": DEFAULT_MODEL,
            "max_total_tokens": DEFAULT_BUDGET,
        })

    def test_explicit_model_and_budget(self):
        config = agent_config(model="gemini-4-argon", budget=50000)
        self.assertEqual(config["model"], "gemini-4-argon")
        self.assertEqual(config["max_total_tokens"], 50000)

    def test_rejects_non_positive_budget(self):
        with self.assertRaises(ValueError):
            agent_config(budget=0)
        with self.assertRaises(ValueError):
            agent_config(budget=-1)

    # Rule 3: the contract carries role, memory rule and report format.
    def test_contract_states_the_three_duties(self):
        contract = worker_contract(project="aistudio-bridge",
                                   repo_dir="/workspace/repo")
        self.assertIn("delegated", contract.lower())
        self.assertIn("memory_search", contract)
        self.assertIn("/workspace/repo", contract)
        self.assertIn("AGENTS.md", contract)
        # the report format the PoC used
        self.assertIn("what you did", contract.lower())
        self.assertIn("evidence", contract.lower())

    def test_contract_forbids_pushing(self):
        contract = worker_contract(project="p", repo_dir="/workspace/r")
        self.assertIn("do not push", contract.lower())


class TestTaskStore(unittest.TestCase):
    def setUp(self):
        if agent_config is None:
            self.fail("RED: remote_worker.state not implemented yet")

    def _store(self, tmp):
        return TaskStore(project="aistudio-bridge", root=tmp)

    # Rule 4: private file, durable across instances.
    def test_state_file_is_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record({"task_id": "t_1", "status": "running"})
            mode = stat.S_IMODE(os.stat(store.path).st_mode)
            self.assertEqual(mode, 0o600)
            reopened = self._store(tmp)
            self.assertEqual(reopened.get("t_1")["status"], "running")

    def test_update_merges_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record({"task_id": "t_1", "status": "running", "tokens": 1})
            store.record({"task_id": "t_1", "status": "completed"})
            task = store.get("t_1")
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["tokens"], 1)

    def test_list_is_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record({"task_id": "t_1", "status": "completed"})
            store.record({"task_id": "t_2", "status": "completed"})
            self.assertEqual([t["task_id"] for t in store.list()],
                             ["t_2", "t_1"])

    # Rule 5: unknown id is an error.
    def test_unknown_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            with self.assertRaises(KeyError):
                store.get("nope")

    def test_records_are_json_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record({"task_id": "t_1", "note": "ñandú ✓"})
            with open(store.path) as fh:
                self.assertIn("ñandú ✓", json.load(fh)["tasks"]["t_1"]["note"])


if __name__ == "__main__":
    unittest.main()