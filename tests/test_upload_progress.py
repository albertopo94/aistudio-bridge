"""S11 follow-up: a multi-minute upload must not be silence.

At the rate the service imposes (~1.1-1.6 MiB/s, measured), a 500 MB tree
takes about six minutes and 1.5 GiB about sixteen. Before this, `run
--local-repo` printed nothing between starting and either returning or being
refused, which is indistinguishable from a hang.

Rules under test:

1. An upload reports where it stands: bytes accepted, bytes total, the rate
   measured from this upload, and the ETA that follows from it. The closing
   line says it finished and how long it took.
2. Progress goes to stderr. stdout stays the report, so
   `remote-worker run ... > report.txt` captures only the report.
3. A run that uploads nothing reports no upload progress.
4. Only bytes the service acknowledged are ever claimed as sent, so an upload
   the window refuses at 290 MiB does not end with a line about 1.5 GiB.
"""

import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import mock

from remote_worker.google_client import (EnvironmentsClient,
                                         InteractionsClient, UploadClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore

#: `sent` and `total` in the progress line, in bytes.
PROGRESS_RE = re.compile(r"(\d+)/(\d+) bytes")


class FakeSandboxServer:
    """The upload surface, speaking the resumable protocol."""

    def __init__(self, refuse_after=None):
        self.requests = []
        self.acknowledged = []
        self._refuse_after = refuse_after
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
                                       "path": self.path,
                                       "headers": dict(self.headers),
                                       "body": body})

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
                        {"id": "v1_p", "object": "interaction",
                         "status": "completed", "usage": {"total_tokens": 1},
                         "steps": []}).encode(),
                        {"Content-Type": "application/json"})
                else:
                    self._reply(200, json.dumps(
                        {"id": "env_p", "status": "active"}).encode(),
                        {"Content-Type": "application/json"})

            def do_DELETE(self):
                self._record()
                self._reply(200, b"")

            def do_PUT(self):
                self._record()
                if "uploadType=resumable" in self.path:
                    self._reply(200, b"", {
                        "Location": f"http://{self.headers['Host']}/session"})
                    return
                span, total = self.headers["Content-Range"].split(" ")[1].split("/")
                end, total = int(span.split("-")[1]), int(total)
                if (outer._refuse_after is not None
                        and len(outer.acknowledged) >= outer._refuse_after):
                    self._reply(400, json.dumps(
                        {"error": {"message": "File size exceeds maximum "
                                              "allowed limit.",
                                   "code": "invalid_request"}}).encode(),
                        {"Content-Type": "application/json"})
                    return
                outer.acknowledged.append(end + 1)
                if end == total - 1:
                    self._reply(200, json.dumps({"files": []}).encode(),
                                {"Content-Type": "application/json"})
                else:
                    self._reply(308, b"", {"Range": f"bytes=0-{end}"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


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


class UploadProgressTest(unittest.TestCase):
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

    def _run_local(self, srv, repo):
        """Run one --local-repo task; the return path is mocked, not under test."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            runner = self._runner(srv)
            with mock.patch("remote_worker.runner.apply_to_branch",
                            return_value={"clone_dir": "c", "diff": "",
                                          "changed": []}), \
                    mock.patch.object(runner, "_prepare_tests_clone",
                                      lambda clone: clone), \
                    mock.patch("remote_worker.runner.run_tests",
                               lambda *a, **k: (0, "")), \
                    mock.patch.object(type(runner.files), "download_to",
                                      lambda *a, **k: None):
                runner.run(task="x", local_repo=repo)
        return out.getvalue(), err.getvalue()

    def test_the_upload_says_where_it_stands(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer() as srv:
            _, err = self._run_local(srv, repo)
        lines = [line for line in err.splitlines() if "upload" in line]
        self.assertTrue(lines, f"no upload progress on stderr:\n{err}")
        for line in lines:
            # Bytes accepted, bytes total, a rate: enough to decide to wait.
            self.assertRegex(line, r"upload \d+/\d+ bytes")
            self.assertRegex(line, r"at (\d+\.\d+|\d+) (MiB|kB|bytes)/s")
        # Every line but the last still has bytes to go, so it owes an ETA.
        for line in lines[:-1]:
            self.assertRegex(line, r"eta ")
        self.assertRegex(lines[-1], r"done in \d")
        # It accumulates: the last line has accepted more than the first.
        sent = [int(PROGRESS_RE.search(line).group(1)) for line in lines]
        self.assertEqual(sent[-1], int(PROGRESS_RE.search(lines[-1]).group(2)))
        if len(lines) > 1:
            self.assertGreater(sent[-1], sent[0], lines)

    def test_progress_never_pollutes_stdout(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer() as srv:
            out, err = self._run_local(srv, repo)
        self.assertTrue([line for line in err.splitlines()
                         if "upload" in line])
        for line in out.splitlines():
            self.assertNotIn("bytes", line, line)
            self.assertNotIn("eta ", line, line)

    def test_only_what_the_service_acknowledged_is_claimed_as_sent(self):
        """An upload the window refuses must not report bytes it never took."""
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer(refuse_after=2) as srv:
            with redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(Exception) as ctx:
                    self._runner(srv).run(task="x", local_repo=repo)
        text = err.getvalue()
        self.assertIn("fair use", str(ctx.exception))
        progress = [line for line in text.splitlines() if "upload" in line]
        self.assertTrue(progress, text)
        accepted = max(srv.acknowledged)
        for line in progress:
            sent, total = (int(g) for g in PROGRESS_RE.search(line).groups())
            self.assertLessEqual(sent, accepted, line)
            self.assertLessEqual(sent, total, line)

    def test_a_cloned_repository_run_reports_no_upload_progress(self):
        """Rule 3: --repo sends no bytes from here, so there is nothing to count."""
        with FakeSandboxServer() as srv:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                runner = self._runner(srv)
                with mock.patch("remote_worker.runner.apply_to_branch",
                                return_value={"clone_dir": "c", "diff": "",
                                              "changed": []}), \
                        mock.patch.object(runner, "_prepare_tests_clone",
                                          lambda clone: clone), \
                        mock.patch("remote_worker.runner.run_tests",
                                   lambda *a, **k: (0, "")), \
                        mock.patch.object(type(runner.files), "download_to",
                                          lambda *a, **k: None):
                    runner.run(task="x",
                               repo_url="https://github.com/o/r")
            self.assertNotIn("uploadType=resumable", " ".join(
                request["path"] for request in srv.requests))
            self.assertNotIn("upload", err.getvalue())


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


if __name__ == "__main__":
    unittest.main()
