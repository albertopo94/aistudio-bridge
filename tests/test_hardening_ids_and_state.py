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