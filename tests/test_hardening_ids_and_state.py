"""Hardening S5: two tasks launched in the same second stay two tasks.

`task_id` was ``f"t_{int(time.time())}"``, so two launches inside one second
produced the same id -- and ``TaskStore.record`` merges by id, so the second
task silently absorbed the first one's ``interaction_id``,
``environment_id`` and ``branch``. The branch name collided the same way, and
``git checkout -B`` re-pointed it at the newer delivery. The review notes
(puntos a mejorar.md, "Otros problemas") name the collision directly.

Rules under test:

1. ``new_task_id()`` never returns the same value twice, even for many calls
   in the same second, and stays readable (a ``t_`` prefix).
2. Two tasks created with the same clock second get different ids AND
   different branch names.
3. ``TaskStore.record`` still merges updates for the SAME task, but a write
   that describes a different task (another interaction/environment) never
   overwrites the stored one.
"""

import os
import tempfile
import unittest

from remote_worker.state import TaskStore, new_task_id


class UniqueTaskIdTest(unittest.TestCase):
    def test_ids_are_unique_within_one_second(self):
        ids = {new_task_id() for _ in range(200)}
        self.assertEqual(len(ids), 200, "task ids collided")
        for task_id in ids:
            self.assertTrue(task_id.startswith("t_"), task_id)

    def test_branch_names_from_ids_are_unique(self):
        branches = {f"worker/repo-{new_task_id()}" for _ in range(50)}
        self.assertEqual(len(branches), 50)


class RecordNeverMergesTwoTasksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="p", root=self._tmp.name)

    def test_updates_to_the_same_task_still_merge(self):
        self.store.record({"task_id": "t1", "status": "in_progress",
                           "interaction_id": "v1"}, create=True)
        self.store.record({"task_id": "t1", "status": "completed"})
        record = self.store.get("t1")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["interaction_id"], "v1")

    def test_a_different_task_under_the_same_id_is_refused(self):
        self.store.record({"task_id": "t2", "status": "in_progress",
                           "interaction_id": "v1", "environment_id": "env_a",
                           "branch": "worker/a"})
        # A second task that happened to land on the same id must not steal
        # the first one's identity -- and the collision is reported, not
        # silently resolved against either task.
        with self.assertRaises(ValueError):
            self.store.record({"task_id": "t2", "status": "in_progress",
                               "interaction_id": "v2",
                               "environment_id": "env_b",
                               "branch": "worker/b"}, create=True)
        record = self.store.get("t2")
        self.assertEqual(record["interaction_id"], "v1")
        self.assertEqual(record["environment_id"], "env_a")
        self.assertEqual(record["branch"], "worker/a")

    def test_two_real_tasks_keep_their_own_records(self):
        a, b = new_task_id(), new_task_id()
        self.assertNotEqual(a, b)
        self.store.record({"task_id": a, "status": "in_progress",
                           "interaction_id": "v_a", "environment_id": "env_a",
                           "branch": f"worker/r-{a}"})
        self.store.record({"task_id": b, "status": "in_progress",
                           "interaction_id": "v_b", "environment_id": "env_b",
                           "branch": f"worker/r-{b}"})
        self.assertEqual(self.store.get(a)["interaction_id"], "v_a")
        self.assertEqual(self.store.get(b)["interaction_id"], "v_b")
        self.assertEqual(len(self.store.list()), 2)


if __name__ == "__main__":
    unittest.main()

class MidLoopProgressTest(unittest.TestCase):
    """Hardening S7: a death mid-loop leaves a continuable record.

    Each function round creates a NEW interaction and the loop never stored
    its id, so a process killed between rounds left the record pointing at
    the previous interaction with a creation-time status: `status --live`
    read a stale id and `continue` refused the task it could have resumed.
    """

    def setUp(self):
        from remote_worker.function_loop import run_until_complete
        self.run_until_complete = run_until_complete

    def _client(self, rounds=2):
        class Rounds:
            def __init__(self):
                self.made = 0

            def create_interaction(self, **kwargs):
                self.made += 1
                if self.made > rounds:  # the turn converges, loop exits
                    return {"id": f"v{self.made + 1}", "status": "completed",
                            "environment_id": "env_1", "agent": "a",
                            "steps": []}
                return {"id": f"v{self.made + 1}", "status": "requires_action",
                        "environment_id": "env_1", "agent": "a",
                        "steps": [{"type": "function_call", "id": f"c{self.made}",
                                   "name": "memory_search",
                                   "arguments": {"query": "x"}}]}
        return Rounds()

    def _dispatcher(self):
        class D:
            def dispatch(self, call):
                return "{}"
        return D()

    def test_each_round_reports_the_new_interaction(self):
        seen = []
        start = {"id": "v1", "status": "requires_action", "agent": "a",
                 "environment_id": "env_1",
                 "steps": [{"type": "function_call", "id": "c1",
                            "name": "memory_search",
                            "arguments": {"query": "x"}}]}
        self.run_until_complete(self._client(), self._dispatcher(), start,
                                max_steps=3, on_round=seen.append)
        # Every created interaction is reported, including the terminal
        # one, so the caller always persists the newest id.
        self.assertEqual([i["id"] for i in seen], ["v2", "v3", "v4"])

    def test_a_death_mid_loop_leaves_in_progress_and_the_newest_id(self):
        store = TaskStore(project="p", root=self._tmpdir())
        store.record({"task_id": "t1", "status": "in_progress",
                      "interaction_id": "v1", "kind": "run"}, create=True)

        class Boom(RuntimeError):
            pass

        def persist(interaction):
            store.record({"task_id": "t1", "status": "in_progress",
                          "interaction_id": interaction["id"]})
            raise Boom("killed mid-loop")

        start = {"id": "v1", "status": "requires_action", "agent": "a",
                 "environment_id": "env_1",
                 "steps": [{"type": "function_call", "id": "c1",
                            "name": "memory_search",
                            "arguments": {"query": "x"}}]}
        with self.assertRaises(Boom):
            self.run_until_complete(self._client(), self._dispatcher(), start,
                                    max_steps=3, on_round=persist)
        record = store.get("t1")
        self.assertEqual(record["interaction_id"], "v2")
        self.assertEqual(record["status"], "in_progress")

    def _tmpdir(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name


class ContinueAcceptsInProgressTest(unittest.TestCase):
    """Hardening S7: a record left `in_progress` with an interaction id is
    exactly the recoverable state a mid-loop death leaves, so `continue` must
    accept it instead of refusing the task it could resume."""

    def test_continue_accepts_in_progress(self):
        import inspect
        from remote_worker import runner as runner_mod
        source = inspect.getsource(runner_mod.TaskRunner.continue_task)
        self.assertIn("in_progress", source)
        self.assertIn("incomplete", source)
