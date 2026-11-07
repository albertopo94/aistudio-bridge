"""S10/S11: the routes code can take into the sandbox are data, not prose.

Two facts drove this (odd/tasks/remote-worker-gaps.md L27-L29):

* The direct-upload ceiling is the account's **fair-use window over a time
  span**, not a size of the file. 1530 MiB passed on fresh quota; 300 MiB
  failed at 290 MiB hours later; 20 MiB passed right after. So a local
  constant claiming "1.5 GiB" is a lie, and the service is the only authority
  on what the account takes today.
* Which route is best changes with the time of day and the user's own
  infrastructure, so the tool must show the options instead of picking one.

Rules under test:

1. One route table is the only source of the limits: every row carries its
   ceiling, its speed, what it needs and whether it actually works here.
2. ``remote-worker routes`` prints that table and needs no API key (the CLI
   stays non-interactive: it informs, it never asks).
3. The service's 400 "File size exceeds maximum allowed limit" is translated
   into what it means — the window, not the file — with the action (retry
   later) and the routes that remain.
4. No local size refusal survives: a tree larger than the old fake 1.5 GiB
   constant is still attempted, because only the service knows.
5. The skill text carries the same rendered table, so the agent's menu and the
   CLI's error message cannot drift apart.
"""

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import mock

from remote_worker import routes, sync
from remote_worker import runner as runner_module
from remote_worker.google_client import (EnvironmentsClient,
                                         InteractionsClient, UploadClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from remote_worker.skill_installer import skill_content
from remote_worker import cli

#: The exact body the service returns when the window is spent, captured live
#: (poc/g5_upload_ceiling.sh and /tmp/g5-timings-300.log).
FAIR_USE_BODY = ('{"error":{"message":"File size exceeds maximum allowed '
                 'limit.","code":"invalid_request"}}')


def _git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                   text=True, check=True)


def _make_repo(root):
    repo = os.path.join(root, "r")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=t",
         "commit", "-qm", "init")
    return repo


class FakeSandboxServer:
    """Creates the environment, then refuses the upload the way the service does."""

    def __init__(self, upload_status=400, upload_body=FAIR_USE_BODY,
                 delete_status=500):
        self.requests = []
        self.upload_status = upload_status
        self.upload_body = upload_body
        # Default 500: a release that the service refused, so the message has
        # to fall back to telling the user how to clean it up himself.
        self.delete_status = delete_status
        self._server = None
        self._thread = None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.requests.append({"method": self.command,
                                       "path": self.path, "body": body})

            def _reply(self, code, payload=b"", headers=None):
                self.send_response(code)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def do_POST(self):
                self._record()
                if self.path.startswith("/v1beta/interactions"):
                    self._reply(200, json.dumps(
                        {"id": "v1_route", "object": "interaction",
                         "status": "completed", "environment_id": None,
                         "usage": {"total_tokens": 3}, "steps": []}
                    ).encode(), {"Content-Type": "application/json"})
                elif self.path.startswith("/v1beta/environments"):
                    self._reply(200, json.dumps({"id": "env_route",
                                                 "status": "active"}).encode(),
                                {"Content-Type": "application/json"})
                else:
                    self._reply(404, b'{"error":"no route"}')

            def do_PUT(self):
                self._record()
                if "uploadType=resumable" in self.path:
                    self._reply(200, b"", {
                        "Location": f"http://{self.headers['Host']}/session"})
                elif self.path.startswith("/session"):
                    if outer.upload_status >= 400:
                        # The service refusing: the case under test.
                        self._reply(outer.upload_status,
                                    outer.upload_body.encode(),
                                    {"Content-Type": "application/json"})
                        return
                    # Otherwise speak the protocol: a non-final chunk answers
                    # 308 with the byte the server committed.
                    content_range = self.headers.get("Content-Range", "")
                    try:
                        span, total = content_range.split(" ")[1].split("/")
                        end, total = int(span.split("-")[1]), int(total)
                    except (ValueError, IndexError):
                        end, total = 0, 0
                    if total and end == total - 1:
                        self._reply(200, outer.upload_body.encode(),
                                    {"Content-Type": "application/json"})
                    else:
                        self._reply(308, b"", {"Range": f"bytes=0-{end}"})
                else:
                    self._reply(404, b'{"error":"no route"}')

            def do_DELETE(self):
                self._record()
                if self.path.startswith("/v1beta/environments/"):
                    self._reply(outer.delete_status, b"")
                else:
                    self._reply(404, b'{"error":"no route"}')

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def paths(self):
        return [request["path"] for request in self.requests]

    def method_paths(self):
        return [f"{request['method']} {request['path']}"
                for request in self.requests]


class RouteTableTest(unittest.TestCase):
    """Rule 1: the table is complete enough to choose from."""

    def test_every_route_states_its_ceiling_speed_needs_and_status(self):
        rows = routes.ROUTES
        self.assertTrue(rows)
        for row in rows:
            for field in ("id", "how", "ceiling", "speed", "needs", "status"):
                self.assertTrue(getattr(row, field), f"{row.id}.{field} empty")
        self.assertEqual(
            [row.id for row in rows],
            ["repository-source", "direct-upload", "inline-files",
             "shards", "gcs-source"])

    def test_the_measured_facts_are_the_measured_ones(self):
        by_id = {row.id: row for row in routes.ROUTES}
        # Documented cap of a `repository` source.
        self.assertIn("500 MB", by_id["repository-source"].ceiling)
        # The upload route must not claim a per-file size: the window is the
        # limit (L29). A number here is the bug this task removes.
        self.assertIn("fair use", by_id["direct-upload"].ceiling)
        self.assertNotIn("GiB", by_id["direct-upload"].ceiling)
        # Shards: proven live on 2026-11-07 (poc/t24_shards.py) -- 3 parts of
        # 1.5 MiB, one turn, and a tree that came back byte-identical -- and
        # still not a command this tool runs. The row must say both, and the
        # proof stays out of the alternatives an error message offers.
        self.assertEqual(by_id["shards"].status, routes.STATUS_PROVEN_POC)
        self.assertIn("poc/t24_shards.py", by_id["shards"].needs)
        # GCS is now a BUILT route, but nothing has run it live (it needs
        # the user's bucket), so it must not be advertised as available:
        # S11 keeps it unvalidated and out of the alternatives line.
        self.assertEqual(by_id["gcs-source"].status, routes.STATUS_UNVALIDATED)
        self.assertEqual(by_id["gcs-source"].how,
                         "--gcs-source gs://bucket/prefix")

    def test_only_the_working_routes_are_offered_as_alternatives(self):
        offered = routes.alternatives_line()
        self.assertIn("--repo", offered)
        self.assertIn("--local-repo", offered)
        self.assertNotIn("gs://", offered)
        self.assertNotIn("shard", offered.lower())


class RoutesCommandTest(unittest.TestCase):
    """Rule 2: the table is reachable offline, so it can be read before spending quota."""

    def test_routes_prints_the_table_without_an_api_key(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["routes"])
        self.assertEqual(code, 0, err.getvalue())
        text = out.getvalue()
        for row in routes.ROUTES:
            self.assertIn(row.how, text)
            self.assertIn(row.ceiling, text)
        self.assertIn(routes.format_routes(), text)


class FairUseTranslationTest(unittest.TestCase):
    """Rules 3 and 4: the service decides, the tool explains."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            uploads=UploadClient(api_key="AQ.k", base_url=srv.base_url,
                                 chunk_size=8),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    def test_the_size_400_becomes_the_window_not_the_file(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer() as srv:
            with self.assertRaises(ValueError) as ctx:
                self._runner(srv).run(task="x", local_repo=repo)
        message = str(ctx.exception)
        self.assertIn("fair use", message)
        self.assertIn("retry", message.lower())
        # It names the routes that remain, and where to read them all.
        self.assertIn("--repo", message)
        self.assertIn("remote-worker routes", message)
        # The sandbox it already created is named, with the way to release it.
        self.assertIn("env_route", message)
        self.assertIn("remote-worker clean", message)
        # And it never repeats the removed fake ceiling.
        self.assertNotIn("GiB", message)

    def test_the_half_used_sandbox_is_released_before_the_error_is_raised(self):
        """A refused upload must not leave a sandbox holding quota for 7 days."""
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer(delete_status=200) as srv:
            with self.assertRaises(ValueError) as ctx:
                self._runner(srv).run(task="x", local_repo=repo)
        self.assertIn("DELETE /v1beta/environments/env_route",
                      srv.method_paths())
        self.assertIn("released", str(ctx.exception))

    def test_a_sandbox_that_cannot_be_released_says_so(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer(delete_status=500) as srv:
            with self.assertRaises(ValueError) as ctx:
                self._runner(srv).run(task="x", local_repo=repo)
        self.assertIn("DELETE /v1beta/environments/env_route",
                      srv.method_paths())
        self.assertIn("clean --yes", str(ctx.exception))

    def test_another_upload_failure_keeps_its_own_explanation(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer(upload_status=403,
                               upload_body='{"error":{"message":"no access"}}'
                               ) as srv:
            with self.assertRaises(Exception) as ctx:
                self._runner(srv).run(task="x", local_repo=repo)
        self.assertNotIn("fair use", str(ctx.exception))

    def test_no_local_ceiling_stops_a_tree_bigger_than_the_old_constant(self):
        """The old guard refused at 1530 MiB without ever asking the service."""
        repo = _make_repo(self._tmp.name)

        def archive_any_size(_repo, dest):
            # A real (tiny) file at the expected path, reported as oversized:
            # what matters here is that no local check stops the attempt.
            with open(dest, "wb") as handle:
                handle.write(b"x" * 32)
            return {"bytes": 2 * 1024 ** 3, "files": 1}

        with FakeSandboxServer(upload_status=200,
                               upload_body='{"files":[]}'
                               ) as srv:
            runner = self._runner(srv)
            with mock.patch("remote_worker.runner.archive_repo",
                            side_effect=archive_any_size):
                with mock.patch("remote_worker.runner.apply_to_branch",
                                return_value={"clone_dir": "c", "diff": "",
                                              "changed": []}), \
                        mock.patch.object(runner, "_prepare_tests_clone",
                                          lambda clone: clone), \
                        mock.patch("remote_worker.runner.run_tests",
                                   lambda *a, **k: (0, "")), \
                        mock.patch.object(type(runner.files), "download_to",
                                          lambda *a, **k: None):
                    result = runner.run(task="x", local_repo=repo)
        self.assertIn("uploadType=resumable", " ".join(srv.paths()))
        self.assertEqual(result["environment_id"], "env_route")

    def test_the_fake_local_ceiling_is_gone(self):
        """Pinned so nobody reintroduces a local number the service never gave."""
        self.assertFalse(hasattr(sync, "UPLOAD_LIMIT_BYTES"))
        self.assertFalse(hasattr(runner_module, "UPLOAD_LIMIT_BYTES"))


class SkillCarriesTheTableTest(unittest.TestCase):
    """Rule 5: one table, three readers (S11)."""

    def test_the_skill_embeds_the_rendered_table(self):
        body = skill_content()
        self.assertIn(routes.format_routes(), body)

    def test_the_readme_embeds_the_rendered_table(self):
        readme = os.path.join(os.path.dirname(routes.__file__), "..", "README.md")
        with open(readme, encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn(routes.format_routes(), body)


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


if __name__ == "__main__":
    unittest.main()
