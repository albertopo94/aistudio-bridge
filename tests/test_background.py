"""G13: long tasks must survive a dropped socket, and continue must not race one.

A foreground request holds one HTTP connection for the whole task; the client
gives up at 600 seconds and the result is lost to the user even though the
sandbox kept working. Background mode returns immediately and is read back
through GET until the interaction reaches a terminal status. The docs name the
running statuses (``in_progress``, ``requires_action``); there is no ``queued``.

Rules under test:

1. The create body carries ``background: true`` when asked, and nothing about
   background when not (the default path stays byte-identical).
2. ``wait_until_done`` polls ``GET /v1beta/interactions/{id}`` until a terminal
   status, and returns that interaction.
3. A memory round trip that happens WHILE polling re-sends the worker contract
   and the tools: G15 has to hold inside the poll loop, not only in the
   foreground loop.
4. Polling is bounded. An interaction that never terminates is reported as the
   WATCH running out, not as a crash, and the task stays resumable — the remote
   work may well finish while nobody is watching.
5. ``ask(background=True)`` uses the same mechanism and returns the worker's
   text once the polled interaction completes; a foreground ``ask`` performs no
   GET at all.
6. ``continue`` on a task the service reports as still running sends no
   continuation. Chaining to an ``in_progress`` interaction is a documented 400,
   so the product says so instead of burning a second branch of the conversation.

The full diff-returning path of a background ``run`` is covered by the existing
sync tests on the foreground path: what this unit adds is how the interaction
reaches a terminal status, not what happens afterwards. Tests here therefore
stop a background run at a non-completed status, which is also what keeps them
off the network.

SSE reconnection (``last_event_id``) is deliberately NOT part of this unit: the
streaming path is one-shot by design and promises no reconnect, and background
plus polling covers the actual failure (a dropped socket) without a new
protocol.
"""

import json
import tempfile
import unittest

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.function_loop import wait_until_done, WatchLimitExceeded
    from remote_worker.runner import TaskRunner
    from remote_worker.state import TaskStore
    from remote_worker.google_client import InteractionsClient
except ImportError:
    wait_until_done = None

AGENT = "antigravity-preview-09-2026"
CONTRACT = ("You are a delegated code worker. Before touching any code, read "
            "the project's AGENTS.md in /workspace/repo-x")
TOOLS = [{"type": "code_execution"}]


def _interaction(status, iid, env="env_bg", tokens=10, steps=None):
    return {"id": iid, "object": "interaction", "agent": AGENT,
            "status": status, "environment_id": env,
            "usage": {"total_tokens": tokens}, "steps": steps or []}


def _call(cid="c_1"):
    return {"type": "function_call", "name": "memory_search", "id": cid,
            "arguments": {"query": "marcador"}}


class StubMemory:
    def tool_specs(self):
        return [{"type": "function", "name": "memory_search",
                 "parameters": {"type": "object", "properties": {}}}]

    def dispatch(self, call):
        return '{"ok": true, "content": "stub"}'


def _client(srv):
    return InteractionsClient(api_key="AQ.k", base_url=srv.base_url)


class WaitUntilDoneTest(unittest.TestCase):
    def setUp(self):
        if wait_until_done is None:
            self.fail("RED: remote_worker.function_loop.wait_until_done missing")

    # Rule 2
    def test_polls_get_until_a_terminal_status(self):
        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                _interaction("in_progress", "v1")).encode()))
            srv.get_replies.append((200, json.dumps(
                _interaction("completed", "v1", tokens=77)).encode()))
            final = wait_until_done(_client(srv), StubMemory(),
                                    _interaction("in_progress", "v1"),
                                    poll_interval=0)
            gets = [r for r in srv.requests if r["method"] == "GET"]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["usage"]["total_tokens"], 77)
        self.assertEqual([g["path"] for g in gets],
                         ["/v1beta/interactions/v1"] * 2)

    def test_a_terminal_interaction_needs_no_poll(self):
        with FakeInteractionsServer() as srv:
            final = wait_until_done(_client(srv), StubMemory(),
                                    _interaction("completed", "v0"),
                                    poll_interval=0)
            gets = [r for r in srv.requests if r["method"] == "GET"]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(gets, [])

    # Rule 3
    def test_contract_and_tools_survive_a_poll_round_trip(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("in_progress", "v2b")
            srv.get_replies.append((200, json.dumps(
                _interaction("requires_action", "v2a",
                             steps=[_call()])).encode()))
            srv.get_replies.append((200, json.dumps(
                _interaction("completed", "v2b")).encode()))
            final = wait_until_done(
                _client(srv), StubMemory(),
                _interaction("in_progress", "v2"),
                poll_interval=0, system_instruction=CONTRACT, tools=TOOLS,
            )
            posts = [json.loads(r["body"]) for r in srv.requests
                     if r["method"] == "POST"]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(posts), 1)
        sent = posts[0]
        self.assertEqual(sent["input"][0]["type"], "function_result")
        self.assertEqual(sent["system_instruction"], CONTRACT)
        self.assertEqual(sent["tools"], TOOLS)
        self.assertEqual(sent["previous_interaction_id"], "v2a")

    # Rule 4
    def test_watch_is_bounded(self):
        with FakeInteractionsServer() as srv:
            for _ in range(40):
                srv.get_replies.append((200, json.dumps(
                    _interaction("in_progress", "v3")).encode()))
            with self.assertRaises(WatchLimitExceeded) as ctx:
                wait_until_done(_client(srv), StubMemory(),
                                _interaction("in_progress", "v3"),
                                poll_interval=0, max_polls=3)
            gets = [r for r in srv.requests if r["method"] == "GET"]
        self.assertEqual(len(gets), 3)
        self.assertIn("3", str(ctx.exception))


class RunnerBackgroundTest(unittest.TestCase):
    def setUp(self):
        if wait_until_done is None:
            self.fail("RED: wait_until_done missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k", client=_client(srv),
            dispatcher=StubMemory(), store=self.store,
            workdir=self._tmp.name, poll_interval=0,
        )

    # Rule 1 + Rule 5
    def test_background_travels_only_when_asked(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("failed", "v_fg")
            self._runner(srv).ask(task="one")
            foreground = json.loads(srv.requests[0]["body"])

            srv.reply_body = _interaction("in_progress", "v_bg")
            srv.get_replies.append((200, json.dumps(
                _interaction("failed", "v_bg")).encode()))
            self._runner(srv).ask(task="two", background=True)
            background = json.loads(srv.requests[1]["body"])
        self.assertNotIn("background", foreground)
        self.assertIs(background.get("background"), True)

    # Rule 5: the foreground path never polls.
    def test_foreground_ask_performs_no_get(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction(
                "completed", "v_fg2",
                steps=[{"type": "model_output",
                        "content": [{"type": "text", "text": "ok"}]}])
            result = self._runner(srv).ask(task="one")
            gets = [r for r in srv.requests if r["method"] == "GET"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(gets, [])

    # Rule 5: background ask reports the polled outcome.
    def test_background_ask_reports_the_polled_status(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("in_progress", "v_bg3")
            srv.get_replies.append((200, json.dumps(
                _interaction("completed", "v_bg3",
                             steps=[{"type": "model_output",
                                     "content": [{"type": "text",
                                                  "text": "the answer"}]}])
                ).encode()))
            result = self._runner(srv).ask(task="summarise", background=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["text"], "the answer")

    # Rule 4 from the runner's side: a lost watch leaves the task resumable.
    def test_run_watch_timeout_leaves_the_task_resumable(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("in_progress", "v_bg4")
            for _ in range(40):
                srv.get_replies.append((200, json.dumps(
                    _interaction("in_progress", "v_bg4")).encode()))
            runner = self._runner(srv)
            runner.max_polls = 2
            result = runner.run(task="do the thing",
                                repo_url="https://github.com/o/repo-x",
                                background=True)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("watch", result["report"].lower())
        self.assertEqual(self.store.get(result["task_id"])["status"],
                         "incomplete")

    # Rule 7
    def test_continue_refuses_to_race_a_running_interaction(self):
        self.store.record({
            "task_id": "t_bg", "kind": "run", "status": "incomplete",
            "interaction_id": "v_live", "environment_id": "env_bg",
            "repo_url": "https://github.com/o/repo-x",
            "repo_dir": "/workspace/repo-x",
            "branch": "worker/repo-x-1", "model": "gemini-3.8-flash",
            "budget": 100000,
        })
        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                _interaction("in_progress", "v_live")).encode()))
            result = self._runner(srv).continue_task(task_id="t_bg")
            posts = [r for r in srv.requests if r["method"] == "POST"]
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("still running", result["report"].lower())
        self.assertEqual(posts, [])
        self.assertEqual(self.store.get("t_bg")["status"], "incomplete")


class StillRunningReportTest(unittest.TestCase):
    """The CLI must not sell a command it just refused.

    A `continue` that finds the interaction still running returns an
    incomplete task for a different reason than a token budget, and advising
    the user to run `continue` again would tell them to do the thing the
    service rejects.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)
        self.store.record({
            "task_id": "t_run", "kind": "run", "status": "incomplete",
            "interaction_id": "v_live", "environment_id": "env_1",
            "repo_url": "https://github.com/o/repo-x",
            "repo_dir": "/workspace/repo-x",
            "branch": "worker/repo-x-1", "model": "gemini-3.8-flash",
            "budget": 100000,
        })

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store,
            workdir=self._tmp.name,
        )

    def test_running_task_is_not_reported_as_a_budget_stop(self):
        from remote_worker import cli
        import io
        from contextlib import redirect_stdout, redirect_stderr

        with FakeInteractionsServer() as srv:
            srv.get_replies.append((200, json.dumps(
                {"id": "v_live", "object": "interaction",
                 "status": "in_progress", "usage": {}, "steps": []}).encode()))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["continue", "t_run"],
                                runner=self._runner(srv))
        printed = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("still running", printed.lower())
        self.assertNotIn("token budget", printed.lower())
        self.assertNotIn("remote-worker continue", printed)

    def test_a_budget_stop_still_gets_the_resume_command(self):
        # The pre-existing behaviour, pinned so the new branch cannot eat it.
        from remote_worker import cli
        import io
        from contextlib import redirect_stdout, redirect_stderr

        self.store.record({"task_id": "t_budget", "kind": "run",
                           "status": "incomplete", "model": "gemini-3.8-flash",
                           "budget": 1000})
        runner = _BudgetOnlyRunner(self.store)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["continue", "t_budget"], runner=runner)
        printed = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("token budget", printed.lower())
        self.assertIn("remote-worker continue t_budget", printed)


class _BudgetOnlyRunner:
    """Stands in for the real runner on the ordinary budget path."""

    def __init__(self, store):
        self.store = store

    def continue_task(self, *, task_id, budget=None, memory_save=False):
        return {"task_id": task_id, "status": "incomplete",
                "tokens": 1000, "budget": 1000, "reason": "budget"}


class BackgroundGuardTest(unittest.TestCase):
    """`--stream` and `--background` cannot both be true.

    Streaming reads the answer off one open connection; background exists to
    avoid holding one. The combination has no documented meaning, so the CLI
    refuses it instead of silently preferring one.
    """

    def _stub(self):
        class NoCall:
            def ask(self, **kw):
                raise AssertionError("the guard must stop before any call")

            def run(self, **kw):
                raise AssertionError("the guard must stop before any call")

        return NoCall()

    def test_stream_plus_background_is_a_usage_error(self):
        from remote_worker import cli
        import io
        from contextlib import redirect_stderr, redirect_stdout

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["ask", "x", "--stream", "--background"],
                         runner=self._stub())
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("cannot be combined", err.getvalue())

    def test_background_reaches_the_runner_for_run_and_ask(self):
        from remote_worker import cli
        import io
        from contextlib import redirect_stdout

        seen = []

        class Recorder:
            def run(self, **kw):
                seen.append(("run", kw.get("background")))
                return {"task_id": "t", "status": "completed"}

            def ask(self, **kw):
                seen.append(("ask", kw.get("background")))
                return {"task_id": "t", "status": "completed", "text": "ok"}

        with redirect_stdout(io.StringIO()):
            cli.main(["run", "x", "--repo", "u", "--background"],
                     runner=Recorder())
            cli.main(["ask", "x", "--background"], runner=Recorder())
            cli.main(["ask", "x"], runner=Recorder())
        self.assertEqual(seen, [("run", True), ("ask", True),
                                ("ask", False)])


if __name__ == "__main__":
    unittest.main()
