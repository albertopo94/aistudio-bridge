"""WU-2: tests for remote_worker.engram_adapter.

Rules under test (from plan WU-2 and the PoC findings):
1. The project is fixed by the orchestrator; a worker-supplied ``project``
   argument can never redirect the memory read or write.
2. A tool result is a JSON *string*, never a list (a list was rejected with
   HTTP 400 by the API during the PoC).
3. Only an allowlisted subset of memory tools is dispatchable; admin tools
   (delete, prune) are unreachable from the worker.
4. CLI failures surface as an error carrying the real stderr, not silence.
5. (hardening S6/S10) Writes are off by default: `memory_save` is neither
   advertised nor dispatchable without the user's opt-in, and an accepted
   write is bounded in size.
"""

import json
import unittest

try:
    from remote_worker.engram_adapter import (
        SAVE_CONTENT_LIMIT,
        SAVE_TITLE_LIMIT,
        EngramError,
        EngramMemory,
        MemoryDispatcher,
        UnknownToolError,
    )
except ImportError:
    EngramMemory = None


class SpyRunner:
    """Records the argv of every CLI call and returns a canned output."""

    def __init__(self, output="Found 1 memories: SOL-VERDE-77"):
        self.calls = []
        self.output = output

    def __call__(self, argv):
        self.calls.append(argv)
        return self.output


class TestEngramMemory(unittest.TestCase):
    def setUp(self):
        if EngramMemory is None:
            self.fail("RED: remote_worker.engram_adapter not implemented yet")

    # Rule 1: the project is pinned by the orchestrator on every call.
    def test_search_pins_project(self):
        runner = SpyRunner()
        memory = EngramMemory(project="aistudio-bridge", runner=runner)
        memory.search("marcador de calibracion")
        argv = runner.calls[0]
        self.assertIn("search", argv)
        self.assertIn("--project", argv)
        self.assertEqual(argv[argv.index("--project") + 1], "aistudio-bridge")
        self.assertIn("marcador de calibracion", argv)

    # Rule 1b: save also pins the project.
    def test_save_pins_project(self):
        runner = SpyRunner(output="Saved observation #999")
        memory = EngramMemory(project="aistudio-bridge", runner=runner)
        memory.save("title", "content")
        argv = runner.calls[0]
        self.assertIn("save", argv)
        self.assertEqual(argv[argv.index("--project") + 1], "aistudio-bridge")

    # Rule 4: a failing CLI call reports the real stderr (default runner).
    def test_cli_failure_raises_engram_error(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "engram")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\necho 'engram: project not found' >&2\nexit 1\n")
            os.chmod(fake, 0o755)
            memory = EngramMemory(project="nope", binary=fake)
            with self.assertRaises(EngramError) as ctx:
                memory.search("anything")
            self.assertIn("project not found", str(ctx.exception))


class TestMemoryDispatcher(unittest.TestCase):
    def setUp(self):
        if EngramMemory is None:
            self.fail("RED: remote_worker.engram_adapter not implemented yet")
        self.runner = SpyRunner()
        self.memory = EngramMemory(project="aistudio-bridge", runner=self.runner)
        self.dispatcher = MemoryDispatcher(self.memory)

    # Rule 2: the result is a JSON string, never a list.
    def test_dispatch_returns_json_string(self):
        call = {
            "type": "function_call",
            "name": "memory_search",
            "id": "call_1",
            "arguments": {"query": "marcador"},
        }
        result = self.dispatcher.dispatch(call)
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])
        self.assertIn("SOL-VERDE-77", parsed["content"])

    # Rule 1: a project argument from the worker is ignored.
    def test_worker_project_argument_is_ignored(self):
        call = {
            "type": "function_call",
            "name": "memory_search",
            "id": "call_1",
            "arguments": {"query": "x", "project": "someone-elses-project"},
        }
        self.dispatcher.dispatch(call)
        argv = self.runner.calls[0]
        self.assertEqual(argv[argv.index("--project") + 1], "aistudio-bridge")
        self.assertNotIn("someone-elses-project", argv)

    # Rule 3: admin tools are unreachable.
    def test_admin_tool_is_rejected(self):
        call = {
            "type": "function_call",
            "name": "memory_delete",
            "id": "call_1",
            "arguments": {"id": 2076},
        }
        with self.assertRaises(UnknownToolError):
            self.dispatcher.dispatch(call)

    # Rule 3b: the exposed tool specs match the allowlist exactly. Hardening
    # S6 narrowed the default to the read-only half; the write is opt-in.
    def test_tool_specs_match_allowlist(self):
        names = {spec["name"] for spec in self.dispatcher.tool_specs()}
        self.assertEqual(names, {"memory_search", "memory_context"})
        opted_in = MemoryDispatcher(self.memory, allow_save=True)
        self.assertEqual(
            {spec["name"] for spec in opted_in.tool_specs()},
            {"memory_search", "memory_save", "memory_context"},
        )


if __name__ == "__main__":
    unittest.main()

class TestMemoryFailureReporting(unittest.TestCase):
    """A broken local store must inform the worker, not kill the task."""

    def setUp(self):
        if EngramMemory is None:
            self.fail("RED: remote_worker.engram_adapter not implemented yet")

    def test_memory_failure_is_reported_not_raised(self):
        def boom(argv):
            raise EngramError("engram: unknown project: missing")

        dispatcher = MemoryDispatcher(EngramMemory(project="missing",
                                                   runner=boom))
        result = dispatcher.dispatch({
            "type": "function_call", "name": "memory_search",
            "id": "call_1", "arguments": {"query": "anything"},
        })
        self.assertIsInstance(result, str)
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertIn("unknown project", payload["error"])


class MemoryWritePolicyTest(unittest.TestCase):
    """Hardening S6 + S10: the worker reads memory; writing is opt-in.

    A prompted agent controls what lands in the user's durable store when
    `memory_save` is advertised: a wrong "decision" it invented, or text
    lifted from a poisoned repository, would outlive the task and steer
    later ones. So the default exposes search and context only, a write
    needs the user's `--memory-save`, and every accepted write is bounded.
    """

    def setUp(self):
        self.runner = SpyRunner()
        self.memory = EngramMemory(project="aistudio-bridge",
                                   runner=self.runner)

    def _call(self, arguments):
        return {"type": "function_call", "name": "memory_save",
                "id": "call_1", "arguments": arguments}

    def test_save_is_not_advertised_by_default(self):
        dispatcher = MemoryDispatcher(self.memory)
        names = {spec["name"] for spec in dispatcher.tool_specs()}
        self.assertEqual(names, {"memory_search", "memory_context"})

    def test_a_write_without_the_opt_in_is_refused_and_saves_nothing(self):
        dispatcher = MemoryDispatcher(self.memory)
        parsed = json.loads(dispatcher.dispatch(
            self._call({"title": "t", "content": "c"})))
        self.assertFalse(parsed["ok"])
        self.assertIn("--memory-save", parsed["error"])
        self.assertEqual(self.runner.calls, [])

    def test_the_opt_in_advertises_and_performs_the_write(self):
        dispatcher = MemoryDispatcher(self.memory, allow_save=True)
        names = {spec["name"] for spec in dispatcher.tool_specs()}
        self.assertIn("memory_save", names)
        parsed = json.loads(dispatcher.dispatch(
            self._call({"title": "t", "content": "c",
                        "type": "decision"})))
        self.assertTrue(parsed["ok"])
        self.assertEqual(self.runner.calls[0][0], "save")

    def test_oversized_content_is_refused_without_touching_the_store(self):
        dispatcher = MemoryDispatcher(self.memory, allow_save=True)
        parsed = json.loads(dispatcher.dispatch(
            self._call({"title": "t",
                        "content": "x" * (SAVE_CONTENT_LIMIT + 1)})))
        self.assertFalse(parsed["ok"])
        self.assertIn("content", parsed["error"])
        self.assertEqual(self.runner.calls, [])

    def test_oversized_title_is_refused(self):
        dispatcher = MemoryDispatcher(self.memory, allow_save=True)
        parsed = json.loads(dispatcher.dispatch(
            self._call({"title": "t" * (SAVE_TITLE_LIMIT + 1),
                        "content": "c"})))
        self.assertFalse(parsed["ok"])
        self.assertIn("title", parsed["error"])
        self.assertEqual(self.runner.calls, [])

    def test_blank_fields_are_refused(self):
        dispatcher = MemoryDispatcher(self.memory, allow_save=True)
        parsed = json.loads(dispatcher.dispatch(
            self._call({"title": "   ", "content": "c"})))
        self.assertFalse(parsed["ok"])
        self.assertEqual(self.runner.calls, [])

    def test_a_boundary_write_is_accepted(self):
        dispatcher = MemoryDispatcher(self.memory, allow_save=True)
        parsed = json.loads(dispatcher.dispatch(
            self._call({"title": "t" * SAVE_TITLE_LIMIT,
                        "content": "x" * SAVE_CONTENT_LIMIT})))
        self.assertTrue(parsed["ok"])
        self.assertEqual(len(self.runner.calls), 1)
