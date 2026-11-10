"""GCS route: a Cloud Storage bucket as the environment source (disabled).

P1-1: the gcs source route is disabled at launch until it has a working git
return path. A gcs tree is not a git repository, so a completed run would call
apply_to_branch() with repo_url="gs://..." and fail a git clone, leaving the
task undelivered with no honest return. ``run --gcs-source`` now refuses with a
clear message. The mount helper stays tested so the entry shape is pinned for
when the route is re-enabled.

Rules under test:

R1. ``sync.mount_gcs(uri, target)`` builds the exact ``{"type": "gcs",
    "source": uri, "target": target}`` entry and rejects a non-``gs://``
    uri and an empty bucket, each with the exact problem.
R2. ``run --gcs-source gs://bkt/tree`` is refused up front with a message
    naming the missing git return path.
R3. CLI guards: ``--gcs-source`` with ``--repo`` or ``--local-repo`` is a
    SystemExit 2 (the mutex group); ``--gcs-source`` with ``--reuse-env`` is
    a SystemExit 2; ``--gcs-source`` with a token file and ``--no-network``
    is a SystemExit 2.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from remote_worker import cli, sync
from remote_worker.google_client import InteractionsClient
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore

from tests.fake_interactions import FakeInteractionsServer


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


class MountGcsTest(unittest.TestCase):
    """R1: the source entry, and what makes it invalid."""

    def test_exact_shape(self):
        self.assertEqual(
            sync.mount_gcs("gs://my-bucket/tree/sub", "/workspace/tree"),
            {"type": "gcs", "source": "gs://my-bucket/tree/sub",
             "target": "/workspace/tree"},
        )

    def test_rejects_non_gs_uri(self):
        with self.assertRaises(ValueError) as ctx:
            sync.mount_gcs("https://example.com/o/r", "/workspace/r")
        self.assertIn("gs://", str(ctx.exception))

    def test_rejects_empty_bucket(self):
        with self.assertRaises(ValueError) as ctx:
            sync.mount_gcs("gs:///prefix", "/workspace/x")
        self.assertIn("bucket", str(ctx.exception).lower())


class GcsRunTest(unittest.TestCase):
    """P1-1: the gcs source route is disabled at launch until it has a
    working git return path. A gcs tree is not a git repository, so a completed
    run would call apply_to_branch() with repo_url="gs://..." and fail a git
    clone, leaving the task undelivered with no honest return. ``run`` refuses
    up front with a clear message instead of offering a route that cannot come
    back."""

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

    def test_gcs_source_is_refused_with_a_clear_message(self):
        with FakeInteractionsServer() as srv:
            with self.assertRaises(ValueError) as ctx:
                self._runner(srv).run(task="x", gcs_source="gs://bkt/tree")
        msg = str(ctx.exception)
        self.assertIn("gcs", msg.lower())
        self.assertIn("return", msg.lower())
        self.assertIn("git", msg.lower())


class GcsCliGuardsTest(unittest.TestCase):
    """R7: the CLI blocks the bad combinations before any request."""

    def test_gcs_source_with_repo_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.build_parser().parse_args(
                ["run", "x", "--gcs-source", "gs://b/t", "--repo", "u"])
        self.assertEqual(ctx.exception.code, 2)

    def test_gcs_source_with_local_repo_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.build_parser().parse_args(
                ["run", "x", "--gcs-source", "gs://b/t", "--local-repo", "."])
        self.assertEqual(ctx.exception.code, 2)

    def test_gcs_source_with_reuse_env_is_a_usage_error(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["run", "x", "--gcs-source", "gs://b/t",
                          "--reuse-env", "env_x"])
        self.assertEqual(ctx.exception.code, 2)

    def test_gcs_token_file_with_no_network_is_a_usage_error(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["run", "x", "--gcs-source", "gs://b/t",
                          "--gcs-token-file", "t", "--no-network"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()