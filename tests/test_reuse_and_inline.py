"""G7 + G22 + G23: reuse a sandbox, and put files inside it.

G7 — ``--reuse-env``. A sandbox can hold installed dependencies; a fresh one
per task reinstalls everything and pays the startup cost again. Rejoining an
existing environment by id is one string instead of a mount specification.

Rules under test:
1. ``--reuse-env <id>`` sends the environment as that id (a string), with no
   sources and no network block — the environment already exists and already
   has its own configuration.
2. Reusing does not skip the repository: the task still needs its code, so
   ``run --reuse-env`` requires a repo and mounts it into the reused sandbox.
   (The service accepts sources alongside an environment id only when they do
   not collide; the product keeps the simple, reviewable shape: id + mounts.)
3. An empty id is a usage error, not a request.

G22 — the repo's AGENTS.md is read only because the worker contract orders the
agent to open it. The service also auto-loads ``.agents/AGENTS.md`` natively,
and the repo mounts at ``/workspace/<name>`` where that does not happen. An
inline source closes the gap without waiting on the contract's good behaviour.

G23 — the documented skill channel is ``.agents/skills/<name>/SKILL.md``, and
the product never used it (its skill installer writes the *local* gentle-shell
skill, a different thing).

Both land as one mechanism:
4. ``inline_source(local_path, target)`` reads a local file and builds an
   ``inline`` source with base64 content and the target path.
5. The service's documented ceilings are enforced before sending: 1 MB per
   file, 2 MB total across inline sources.
6. Targets must be inside the sandbox and must not be the filesystem root;
   ``.agents/AGENTS.md`` (relative) resolves under the working directory the
   same way a mount target does, so an absolute path is required to be
   explicit and validated.
7. The local file must exist and be text — a binary or missing path fails with
   the reason, before any network call.
8. The CLI wires ``--agents-file`` (a shortcut for the AGENTS.md target) and
   ``--mount-file LOCAL:TARGET`` (repeatable) into ``run``.
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
    from remote_worker.runner import TaskRunner
    from remote_worker.state import TaskStore
    from remote_worker.sync import (inline_source, INLINE_FILE_LIMIT_BYTES,
                                INLINE_TOTAL_LIMIT_BYTES)
except ImportError:
    cli = None


def _interaction(status="failed", iid="v1", env="env_1"):
    return {"id": iid, "object": "interaction", "status": status,
            "environment_id": env, "usage": {"total_tokens": 5}, "steps": []}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


class ReuseEnvTest(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker missing")
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

    # Rule 1 + 2
    def test_reuse_sends_the_id_and_still_mounts_the_repo(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            self._runner(srv).run(
                task="x", repo_url="https://github.com/o/r",
                reuse_env="env_hot",
            )
            env = json.loads(srv.requests[0]["body"])["environment"]
        self.assertEqual(env["id"], "env_hot")
        self.assertEqual(env["sources"][0]["target"], "/workspace/r")
        self.assertNotIn("type", env)   # an existing environment is not "remote"

    def test_reuse_records_the_environment_it_used(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction(env="env_hot")
            result = self._runner(srv).run(
                task="x", repo_url="https://github.com/o/r",
                reuse_env="env_hot",
            )
        self.assertEqual(self.store_env(result["task_id"]), "env_hot")

    def store_env(self, task_id):
        return TaskStore(project="proj", root=self._tmp.name).get(task_id).get(
            "environment_id")

    # Rule 3: the CLI rejects an empty id before any request.
    def test_empty_id_is_a_usage_error(self):
        class NoCall:
            def run(self, **kw):
                raise AssertionError("must not reach the runner")

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["run", "x", "--repo", "u", "--reuse-env", " "],
                         runner=NoCall())
        self.assertEqual(ctx.exception.code, 2)


class InlineSourceTest(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, text):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    # Rule 4
    def test_inline_source_shape(self):
        path = self._write("AGENTS.md", "# Norms\n")
        source = inline_source(path, "/.agents/AGENTS.md")
        self.assertEqual(source["type"], "inline")
        self.assertEqual(source["target"], "/.agents/AGENTS.md")
        self.assertEqual(base64.b64decode(source["content"]).decode(),
                         "# Norms\n")
        self.assertEqual(source.get("encoding"), "base64")

    # Rule 5
    def test_per_file_ceiling(self):
        path = self._write("big.md", "x" * (INLINE_FILE_LIMIT_BYTES + 1))
        with self.assertRaises(ValueError) as ctx:
            inline_source(path, "/.agents/AGENTS.md")
        self.assertIn("1 MB", str(ctx.exception))

    def test_total_ceiling_across_sources(self):
        big = "x" * (INLINE_FILE_LIMIT_BYTES - 1024)
        two = self._write("b.md", big)
        # One nearly-full file already counted toward the 2 MB total, so a
        # second near-full file no longer fits.
        with self.assertRaises(ValueError) as ctx:
            inline_source(two, "/.agents/b.md",
                          total_bytes=INLINE_TOTAL_LIMIT_BYTES - 512)
        self.assertIn("total", str(ctx.exception).lower())

    # Rule 6
    def test_targets_must_be_absolute_and_not_root(self):
        path = self._write("s.md", "x")
        for bad in ["", "/", ".agents/AGENTS.md", "../escape.md"]:
            with self.assertRaises(ValueError, msg=bad):
                inline_source(path, bad)

    # Rule 7
    def test_missing_file_reports_the_reason(self):
        with self.assertRaises(ValueError) as ctx:
            inline_source(os.path.join(self._tmp.name, "nope.md"),
                          "/.agents/nope.md")
        self.assertIn("nope.md", str(ctx.exception))

    def test_binary_content_is_refused(self):
        path = os.path.join(self._tmp.name, "blob.md")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe\x00binary")
        with self.assertRaises(ValueError) as ctx:
            inline_source(path, "/.agents/blob.md")
        self.assertIn("text", str(ctx.exception).lower())


class InlineWireTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.agents = os.path.join(self._tmp.name, "AGENTS.md")
        with open(self.agents, "w", encoding="utf-8") as fh:
            fh.write("# Project norms\n")

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    # Rule 8 on the wire
    def test_agents_file_travels_as_an_inline_source(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            self._runner(srv).run(task="x",
                                  repo_url="https://github.com/o/r",
                                  agents_file=self.agents)
            sources = json.loads(srv.requests[0]["body"])["environment"][
                "sources"]
        repo = [s for s in sources if s["type"] == "repository"]
        inline = [s for s in sources if s["type"] == "inline"]
        self.assertEqual(len(repo), 1)
        self.assertEqual(inline[0]["target"], "/.agents/AGENTS.md")

    def test_mount_file_accepts_local_target_pairs(self):
        skill = os.path.join(self._tmp.name, "SKILL.md")
        with open(skill, "w", encoding="utf-8") as fh:
            fh.write("---\nname: x\n---\n")
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            self._runner(srv).run(
                task="x", repo_url="https://github.com/o/r",
                # The runner takes split pairs; the CLI splits LOCAL:TARGET.
                mount_files=[(skill,
                              "/workspace/.agents/skills/x/SKILL.md")],
            )
            sources = json.loads(srv.requests[0]["body"])["environment"][
                "sources"]
        inline = [s for s in sources if s["type"] == "inline"]
        self.assertEqual(inline[0]["target"],
                         "/workspace/.agents/skills/x/SKILL.md")

    # A bad pair is a usage error, decided before anything is read or sent.
    def test_malformed_mount_file_pair_is_a_usage_error(self):
        class NoCall:
            def run(self, **kw):
                raise AssertionError("must not reach the runner")

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["run", "x", "--repo", "u", "--mount-file",
                          "no-colon-here"], runner=NoCall())
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("LOCAL:TARGET", err.getvalue())


if __name__ == "__main__":
    unittest.main()
