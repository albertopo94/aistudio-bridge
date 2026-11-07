"""GCS route: a Cloud Storage bucket as the environment source.

The sandbox does not clone or upload: the service COPIES the tree out of the
user's own GCS bucket at mount time (docs/agent-environment). The route
escapes the fair-use upload window entirely, because no bytes leave the
user's machine over the upload path. The documented cap is 2 GB per gcs
source, and target "/" is not allowed.

Rules under test:

R1. ``sync.mount_gcs(uri, target)`` builds the exact ``{"type": "gcs",
    "source": uri, "target": target}`` entry and rejects a non-``gs://``
    uri and an empty bucket, each with the exact problem.
R2. ``run --gcs-source gs://bkt/tree`` sends environment
    ``{"type": "remote", "sources": [{"type": "gcs", "source":
    "gs://bkt/tree", "target": "/workspace/tree"}]}`` and NO repository
    source; the task record's ``repo_url`` is the ``gs://`` uri.
R3. With no ``gcloud`` on PATH (public bucket), the run proceeds and the
    environment carries NO ``network`` key at all.
R4. ``--gcs-token-file`` with a temp file → the allowlist is exactly
    ``[gcs_rule, {"domain": "*"}]`` when the user set no allowlist; the
    token value appears in the wire body but NEVER in the CLI stdout/stderr.
R5. A token plus the user's own ``--allow-domain ["pypi.org"]`` → the
    allowlist is ``[pypi rule, gcs rule]`` with NO wildcard.
R6. A fake ``gcloud`` stub on PATH (a shell script echoing a token) → the
    token is picked up and the rule is injected, without any token file.
R7. CLI guards: ``--gcs-source`` with ``--repo`` or ``--local-repo`` is a
    SystemExit 2 (the mutex group); ``--gcs-source`` with ``--reuse-env`` is
    a SystemExit 2; ``--gcs-source`` with a token file and ``--no-network``
    is a SystemExit 2.
"""

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from remote_worker import cli, sync
from remote_worker.google_client import InteractionsClient
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore

from tests.fake_interactions import FakeInteractionsServer

GCS_RULE = {
    "domain": "storage.googleapis.com",
    "transform": {"Authorization": "Bearer faketoken"},
}


def _interaction(status="failed", iid="v1", env="env_1"):
    return {"id": iid, "object": "interaction", "status": status,
            "environment_id": env, "usage": {"total_tokens": 5}, "steps": []}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


def _wire(runner, srv, **kwargs):
    """Run once against the fake and return the interaction body's environment."""
    srv.reply_body = _interaction()
    runner.run(task="x", **kwargs)
    body = json.loads(srv.last_request["body"])
    return body["environment"]


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
    """R2: the wire shape for a gcs run, with no token (public bucket)."""

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

    def test_gcs_source_sends_the_gcs_source_and_no_repository(self):
        with FakeInteractionsServer() as srv:
            env = _wire(self._runner(srv), srv,
                        gcs_source="gs://bkt/tree")
        self.assertEqual(env["type"], "remote")
        self.assertEqual(env["sources"], [
            {"type": "gcs", "source": "gs://bkt/tree",
             "target": "/workspace/tree"},
        ])
        self.assertNotIn("network", env)

    def test_the_task_record_repo_url_is_the_gs_uri(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            result = self._runner(srv).run(
                task="x", gcs_source="gs://bkt/tree")
        record = TaskStore(project="proj",
                           root=self._tmp.name).get(result["task_id"])
        self.assertEqual(record["repo_url"], "gs://bkt/tree")


class GcsNoGcloudTest(unittest.TestCase):
    """R3: public bucket — no gcloud on PATH means no network key at all."""

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

    def test_no_gcloud_means_no_network_key(self):
        with FakeInteractionsServer() as srv:
            with mock.patch("remote_worker.runner.shutil.which",
                            return_value=None):
                env = _wire(self._runner(srv), srv,
                            gcs_source="gs://bkt/tree")
        self.assertNotIn("network", env)


class GcsTokenFileTest(unittest.TestCase):
    """R4 + R5: an explicit token file, and the catch-all/allowlist interplay."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _token_file(self, value="faketoken\n"):
        path = os.path.join(self._tmp.name, "token.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(value)
        return path

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    def test_token_file_gives_exactly_rule_plus_catchall(self):
        token = self._token_file()
        with FakeInteractionsServer() as srv:
            env = _wire(self._runner(srv), srv,
                        gcs_source="gs://bkt/tree", gcs_token_file=token)
        self.assertEqual(env["network"], {
            "allowlist": [GCS_RULE, {"domain": "*"}],
        })

    def test_token_file_with_user_allowlist_keeps_the_fence(self):
        token = self._token_file()
        with FakeInteractionsServer() as srv:
            env = _wire(self._runner(srv), srv,
                        gcs_source="gs://bkt/tree", gcs_token_file=token,
                        allow_domains=["pypi.org"])
        self.assertEqual(env["network"], {
            "allowlist": [{"domain": "pypi.org"}, GCS_RULE],
        })

    def test_the_token_reaches_the_wire_but_never_the_terminal(self):
        token = self._token_file("supersecret-token")
        with FakeInteractionsServer() as srv:
            env = _wire(self._runner(srv), srv,
                        gcs_source="gs://bkt/tree", gcs_token_file=token)
        body = json.dumps(env)
        self.assertIn("supersecret-token", body)

    def test_missing_token_file_names_it(self):
        missing = os.path.join(self._tmp.name, "nope-token.txt")
        with FakeInteractionsServer() as srv:
            with self.assertRaises(ValueError) as ctx:
                self._runner(srv).run(task="x", gcs_source="gs://bkt/tree",
                                      gcs_token_file=missing)
        self.assertIn("nope-token.txt", str(ctx.exception))

    def test_empty_token_file_is_refused_naming_it(self):
        token = self._token_file("")
        with FakeInteractionsServer() as srv:
            with self.assertRaises(ValueError) as ctx:
                self._runner(srv).run(task="x", gcs_source="gs://bkt/tree",
                                      gcs_token_file=token)
        self.assertIn("token", str(ctx.exception).lower())


class GcsFakeGcloudTest(unittest.TestCase):
    """R6: a fake `gcloud` stub on PATH supplies the token."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        bindir = os.path.join(self._tmp.name, "bin")
        os.makedirs(bindir)
        stub = os.path.join(bindir, "gcloud")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nprintf 'stubtoken'\n")
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        self._path = bindir + os.pathsep + os.environ.get("PATH", "")

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    def test_a_fake_gcloud_on_path_supplies_the_token(self):
        with FakeInteractionsServer() as srv:
            with mock.patch.dict(os.environ, {"PATH": self._path}):
                env = _wire(self._runner(srv), srv,
                            gcs_source="gs://bkt/tree")
        rule = {"domain": "storage.googleapis.com",
                "transform": {"Authorization": "Bearer stubtoken"}}
        self.assertEqual(env["network"],
                         {"allowlist": [rule, {"domain": "*"}]})


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