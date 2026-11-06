"""G11/G28: send a picture with the task. G24: pin which tools each mode gets.

G11 — "look at this screenshot with the error" is the canonical task this
product cannot do today: `run` and `ask` send a string, and the agent can only
read files it already has. The documented input for Antigravity is text plus
base64 image parts; Deep Research adds documents, and the same builder serves
both (G28), so the parts builder takes a list and this product only offers the
image kind its agent actually accepts.

Rules under test:
1. ``task_parts("...", [png])`` returns ``[{text}, {image with mime_type}]``
   in that order, the text first.
2. Without images it returns the plain string, exactly as before: the default
   request body must not change shape.
3. The mime type comes from the file, not from its extension alone, and an
   unsupported kind (audio, video, PDF, unknown) is refused with the reason —
   Antigravity reads text and image files only.
4. Our own size guard rejects an absurd file before it is read into a request,
   and says it is ours, not the service's.
5. ``run`` and ``ask`` both accept images, and they reach the `input` field.
6. A missing file is a usage error, decided before any request.

G24 — the service treats ``tools`` as a REPLACEMENT for its defaults, not an
addition. That makes the mode difference real and invisible in the README:
``run`` has code execution and memory only (no web), ``ask`` adds search and
URL context. Pinned so the difference stays a decision instead of drifting.

Rules under test:
7. ``run`` sends code_execution plus the memory tools, and no web tools.
8. ``ask`` sends code_execution, google_search and url_context plus memory.
9. The README states the difference (asserted here so docs and behaviour cannot
   drift apart silently).
"""

import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker import cli
    from remote_worker.google_client import InteractionsClient
    from remote_worker.planner import task_parts
    from remote_worker.runner import TaskRunner
    from remote_worker.state import TaskStore
except ImportError:
    cli = None

# A 1x1 transparent PNG, written as bytes so the test does not depend on any
# fixture file in the repository.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _interaction(status="failed", iid="v1", env="env_1"):
    return {"id": iid, "object": "interaction", "status": status,
            "environment_id": env, "usage": {"total_tokens": 5}, "steps": []}


class StubMemory:
    def tool_specs(self):
        return [{"type": "function", "name": "memory_search",
                 "parameters": {"type": "object", "properties": {}}}]

    def dispatch(self, call):
        return "{}"


class TaskPartsTest(unittest.TestCase):
    def setUp(self):
        if task_parts is None:
            self.fail("RED: planner.task_parts missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, data):
        path = os.path.join(self._tmp.name, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    # Rule 1
    def test_text_then_image_parts(self):
        png = self._write("shot.png", PNG)
        parts = task_parts("what is wrong here?", [png])
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], {"type": "text",
                                    "text": "what is wrong here?"})
        self.assertEqual(parts[1]["type"], "image")
        self.assertEqual(parts[1]["mime_type"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["data"]), PNG)

    # Rule 2
    def test_no_images_keeps_the_plain_string(self):
        self.assertEqual(task_parts("just text", None), "just text")
        self.assertEqual(task_parts("just text", []), "just text")

    def test_several_images_keep_their_order(self):
        a = self._write("a.png", PNG)
        b = self._write("b.png", PNG)
        parts = task_parts("compare", [a, b])
        self.assertEqual([p["type"] for p in parts],
                         ["text", "image", "image"])

    # Rule 3
    def test_non_image_kind_is_refused_with_the_reason(self):
        for name, data in [("clip.mp3", b"ID3junk"), ("movie.mp4", b"junk"),
                           ("paper.pdf", b"%PDF-1.4\njunk"),
                           ("mystery", b"\x00\x01\x02")]:
            path = self._write(name, data)
            with self.assertRaises(ValueError, msg=name) as ctx:
                task_parts("look", [path])
            message = str(ctx.exception)
            self.assertIn(name, message)
            self.assertIn("image", message.lower())

    def test_mime_comes_from_the_bytes_not_the_extension(self):
        # A PNG lying about being a .jpg must not be sent as image/jpeg.
        sneaky = self._write("sneaky.jpg", PNG)
        parts = task_parts("look", [sneaky])
        self.assertEqual(parts[1]["mime_type"], "image/png")

    # Rule 4
    def test_oversized_image_is_refused_by_our_own_guard(self):
        from remote_worker.planner import IMAGE_INPUT_LIMIT_BYTES
        big = self._write("big.png", PNG + b"\x00" * IMAGE_INPUT_LIMIT_BYTES)
        with self.assertRaises(ValueError) as ctx:
            task_parts("look", [big])
        self.assertIn("20 MB", str(ctx.exception))

    def test_empty_text_is_allowed_with_an_image(self):
        png = self._write("only.png", PNG)
        parts = task_parts("", [png])
        self.assertEqual(parts[0]["type"], "text")
        self.assertEqual(parts[0]["text"], "")


class ImageWireTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.png = os.path.join(self._tmp.name, "shot.png")
        with open(self.png, "wb") as fh:
            fh.write(PNG)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    # Rule 5
    def test_ask_sends_the_image(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("completed")
            self._runner(srv).ask(task="what broke?", images=[self.png])
            body = json.loads(srv.requests[0]["body"])
        self.assertIsInstance(body["input"], list)
        self.assertEqual(body["input"][1]["mime_type"], "image/png")

    def test_run_sends_the_image(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            self._runner(srv).run(task="fix the screen",
                                  repo_url="https://github.com/o/r",
                                  images=[self.png])
            body = json.loads(srv.requests[0]["body"])
        self.assertEqual(body["input"][1]["type"], "image")

    def test_no_images_sends_a_string(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("completed")
            self._runner(srv).ask(task="plain")
            body = json.loads(srv.requests[0]["body"])
        self.assertEqual(body["input"], "plain")

    # Rule 6: the CLI decides before any request goes out.
    def test_missing_image_is_a_usage_error(self):
        class NoCall:
            def ask(self, **kw):
                raise AssertionError("must not reach the runner")

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["ask", "look", "--image", "/nope/missing.png"],
                         runner=NoCall())
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("missing.png", err.getvalue())


class ToolSetTest(unittest.TestCase):
    """G24: `tools` replaces the service defaults, so the mode difference is
    real. Pinned so nobody can quietly give the code worker a web tool (or
    take one away from research) without the tests noticing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    # Rule 7
    def test_run_has_code_and_memory_but_no_web(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            self._runner(srv).run(task="x",
                                  repo_url="https://github.com/o/r")
            tools = json.loads(srv.requests[0]["body"])["tools"]
        kinds = [t.get("type") for t in tools]
        self.assertIn("code_execution", kinds)
        self.assertIn("function", kinds)
        self.assertNotIn("google_search", kinds)
        self.assertNotIn("url_context", kinds)

    # Rule 8
    def test_ask_adds_the_web_tools(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("completed")
            self._runner(srv).ask(task="x")
            tools = json.loads(srv.requests[0]["body"])["tools"]
        kinds = [t.get("type") for t in tools]
        for expected in ("code_execution", "google_search", "url_context",
                         "function"):
            self.assertIn(expected, kinds)

    # Rule 9: the docs must not drift from the behaviour.
    def test_readme_states_that_tools_replace_the_defaults(self):
        with open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("run vs ask", readme)
        lowered = readme.lower()
        self.assertIn("replac", lowered)          # tools replace the defaults
        self.assertIn("google_search", readme)     # the tool set table


if __name__ == "__main__":
    unittest.main()
