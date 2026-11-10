"""P0-2 (T5): a task's workspace, state writes and memory permission must be
isolated from any other concurrent task.

A single-process CLI creates a fresh TaskRunner per invocation, but the
product's goal is a concurrent remote runtime (a server / MCP). The defects
P0-2 names are the places where two tasks, even in separate processes, would
step on each other:

1. **Delivery workspace**: `_deliver` applied the workspace into a shared
   `work/clone`. Two tasks of the same project raced on the same clone
   directory (re-clone, branch reset, `_drop_removed`). Each task must have
   its own delivery clone, keyed by task id.
2. **State writes**: `TaskStore.record` is a read-modify-write of one
   `state.json`. Two processes both load the same snapshot, both save; the
   last writer silently drops the other's fields. Writes must serialize under
   a cross-process lock so no update is lost.
3. **Memory permission**: `MemoryDispatcher.allow_save` was a mutable field
   on a shared dispatcher. A task-scoped permission must be immutable per
   task, so one task can never change another's permission.
"""

import json
import multiprocessing
import os
import tempfile
import unittest

from remote_worker.engram_adapter import MemoryDispatcher
from remote_worker.google_client import (EnvironmentsClient, FilesClient,
                                         InteractionsClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from tests.fake_interactions import FakeInteractionsServer
from tests.test_delivery_failure import _make_origin, _worker_tar_bytes

ENV_ID = "env_conc"


class FakeClient:
    """Captures the tools of every interaction it is asked to create."""

    def __init__(self):
        self.tools_sent = []

    def create_interaction(self, *, agent=None, input=None, environment=None,
                           agent_config=None, system_instruction=None,
                           tools=None, background=False,
                           previous_interaction_id=None):
        self.tools_sent.append([t.get("name") for t in (tools or [])
                                if t.get("type") == "function"])
        return {"id": f"v{len(self.tools_sent)}", "status": "completed",
                "environment_id": ENV_ID,
                "usage": {"total_tokens": 1},
                "steps": [{"type": "model_output",
                           "content": [{"type": "text", "text": "ok"}]}]}

    def usage(self, interaction):
        return interaction.get("usage") or {}

    def last_model_output(self, interaction):
        return "ok"


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


def _interaction(interaction_id="v1_conc", status="completed"):
    return {"id": interaction_id, "status": status,
            "environment_id": ENV_ID, "usage": {"total_tokens": 4000},
            "steps": [{"type": "model_output",
                       "content": [{"type": "text", "text": "done"}]}]}


# --- D1: per-task delivery workspace -----------------------------------


class PerTaskDeliveryCloneTest(unittest.TestCase):
    """Each task's delivery applies into its own clone, never a shared one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = _make_origin(self.tmp)
        self.store = TaskStore(project="proj", root=self.tmp)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=FilesClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store, workdir=self.tmp,
        )

    def _seed_incomplete(self, task_id, interaction_id):
        self.store.record({
            "task_id": task_id, "kind": "run", "status": "incomplete",
            "interaction_id": interaction_id, "environment_id": ENV_ID,
            "repo_url": self.origin, "repo_dir": "/workspace/origin",
            "branch": f"worker/{task_id}", "model": "gemini-3.8-flash",
            "budget": 9000,
        })

    def _serve(self, srv, interaction):
        srv.get_replies = [(200, json.dumps({"status": "completed"}).encode())]
        srv.get_replies.append((200, _worker_tar_bytes()))
        srv.reply_body = interaction

    def test_each_task_applies_into_its_own_clone(self):
        clones = []
        for task_id, interaction_id in [
                ("t_conc_a", "v_conc_a"), ("t_conc_b", "v_conc_b")]:
            self._seed_incomplete(task_id, interaction_id)
            with FakeInteractionsServer() as srv:
                self._serve(srv, _interaction(interaction_id))
                result = self._runner(srv).continue_task(task_id=task_id)
            self.assertEqual(result["status"], "completed")
            clones.append(result["clone_dir"])
        self.assertNotEqual(clones[0], clones[1])
        # No shared work/clone was ever used: the only clones are per-task.
        for task_id in ("t_conc_a", "t_conc_b"):
            own = os.path.join(self.tmp, task_id, "clone")
            self.assertTrue(os.path.isdir(os.path.join(own, ".git")))


# --- D2: state writes are safe under concurrency -----------------------


def _child_record(root, project, task_id, interaction_id, conn):
    """A child process adds one task record to the shared state.json."""
    try:
        TaskStore(project=project, root=root).record({
            "task_id": task_id, "status": "in_progress",
            "interaction_id": interaction_id,
        })
        conn.send(("ok", None))
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        conn.send(("err", f"{type(exc).__name__}: {exc}"))


class ConcurrentStateWritesTest(unittest.TestCase):
    """Two processes writing the same state.json must not lose an update."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.ctx = multiprocessing.get_context("spawn")

    def test_concurrent_records_all_survive(self):
        tasks = [(f"t_c{i}", f"v_c{i}") for i in range(4)]
        procs, pipes = [], []
        for task_id, interaction_id in tasks:
            recv, send = multiprocessing.Pipe(False)
            pipes.append(recv)
            p = self.ctx.Process(
                target=_child_record,
                args=(self.tmp, "proj", task_id, interaction_id, send),
            )
            procs.append(p)
            p.start()
        errors = []
        for recv, p in zip(pipes, procs):
            p.join(30)
            if p.is_alive():
                p.terminate()
                errors.append(f"{p.pid}: did not finish")
            elif recv.poll():
                status, msg = recv.recv()
                if status != "ok":
                    errors.append(msg)
        if errors:
            self.fail("concurrent record failed: " + "; ".join(errors))
        store = TaskStore(project="proj", root=self.tmp)
        ids = {t["task_id"] for t in store.list()}
        self.assertEqual(ids, {t for t, _ in tasks})


# --- D3: memory permission is immutable and task-scoped ----------------


class _NoopMemory:
    def context(self, scope=None):
        return ""

    def search(self, query, **kwargs):
        return ""

    def save(self, *args, **kwargs):
        return ""


class ImmutableMemoryPermissionTest(unittest.TestCase):
    """allow_save is set once per dispatcher and cannot be flipped."""

    def test_permission_is_a_read_only_property(self):
        d = MemoryDispatcher(_NoopMemory(), allow_save=True)
        self.assertTrue(d.allow_save)
        # A second task on the same dispatcher must not be able to flip it.
        with self.assertRaises(AttributeError):
            d.allow_save = False

    def test_two_dispatchers_keep_their_own_permission(self):
        yes = MemoryDispatcher(_NoopMemory(), allow_save=True)
        no = MemoryDispatcher(_NoopMemory(), allow_save=False)
        self.assertTrue(yes.allow_save)
        self.assertFalse(no.allow_save)

    def test_one_runner_two_tasks_no_permission_bleed(self):
        """Two tasks on the SAME runner, opposite --memory-save flags.

        The second task must never advertise memory_save just because the
        first task asked for it: the permission is per-task and immutable.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        origin = _make_origin(tmp.name)
        client = FakeClient()
        runner = TaskRunner(
            project="proj", api_key="AQ.k", client=client,
            store=TaskStore(project="proj", root=tmp.name),
            workdir=tmp.name,
        )
        runner.run(task="a", repo_url=origin, memory_save=True)
        runner.run(task="b", repo_url=origin, memory_save=False)
        self.assertIn("memory_save", client.tools_sent[0])
        self.assertNotIn("memory_save", client.tools_sent[1])


if __name__ == "__main__":
    unittest.main()