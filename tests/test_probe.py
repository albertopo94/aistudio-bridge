"""S12: `probe` answers whether this account can upload right now.

The upload ceiling is the account's fair-use window over a time span, so it
cannot be read from anywhere: it is only observable by attempting an upload.
That makes a 1.5 GiB attempt the only honest test of it, which is a bad way to
find out at 1.5 MiB/s. A 1 MiB attempt costs about a second and answers the
same question.

Rules under test:

1. `probe` creates a scratch sandbox, uploads ~1 MiB to it, deletes it, and
   answers in one line: uploads work, or they are blocked, with the rate the
   service allowed.
2. The sandbox is deleted on every exit path, including a blocked upload and a
   failed one. A probe that leaves sandboxes behind would itself become the
   thing that fills the account.
3. No interaction is created: a check that ran a task would spend the wrong
   quota to answer a question about bytes.
4. A blocked window is reported as the window, with the action, and exits with
   its own code, so a script can branch on it.
5. When the sandbox cannot be deleted, the message says so and names
   `clean --yes`: a held sandbox is the user's problem to know about.
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from remote_worker.google_client import (EnvironmentsClient,
                                         InteractionsClient, UploadClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from remote_worker import cli

FAIR_USE_BODY = ('{"error":{"message":"File size exceeds maximum allowed '
                 'limit.","code":"invalid_request"}}')


class FakeSandboxServer:
    """A sandbox that either takes the probe upload or refuses it."""

    def __init__(self, upload_status=200, upload_body="", delete_status=200):
        self.requests = []
        self.upload_status = upload_status
        self.upload_body = upload_body
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
                    self._reply(200, json.dumps({"id": "v1_probe",
                                                 "steps": []}).encode(),
                                {"Content-Type": "application/json"})
                else:
                    self._reply(200, json.dumps({"id": "env_probe",
                                                 "status": "active"}).encode(),
                                {"Content-Type": "application/json"})

            def do_DELETE(self):
                self._record()
                self._reply(outer.delete_status, b"")

            def do_PUT(self):
                self._record()
                if "uploadType=resumable" in self.path:
                    self._reply(200, b"", {
                        "Location": f"http://{self.headers['Host']}/session"})
                elif self.path.startswith("/session"):
                    if outer.upload_status >= 400:
                        self._reply(outer.upload_status,
                                    outer.upload_body.encode(),
                                    {"Content-Type": "application/json"})
                        return
                    # Speak the resumable protocol: a non-final chunk owes a
                    # 308 with the byte committed, only the last one answers 200.
                    span, total = self.headers["Content-Range"].split(" ")[1].split("/")
                    end, total = int(span.split("-")[1]), int(total)
                    if end == total - 1:
                        self._reply(200, json.dumps({"files": []}).encode(),
                                    {"Content-Type": "application/json"})
                    else:
                        self._reply(308, b"", {"Range": f"bytes=0-{end}"})
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

    def method_paths(self):
        return [f"{request['method']} {request['path']}"
                for request in self.requests]

    def uploaded_bytes(self):
        return sum(len(request["body"]) for request in self.requests
                   if request["method"] == "PUT"
                   and request["path"].startswith("/session"))


class ProbeTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            uploads=UploadClient(api_key="AQ.k", base_url=srv.base_url,
                                 chunk_size=512 * 1024),
            dispatcher=StubMemory(),
            store=TaskStore(project="proj", root=self._tmp.name),
            workdir=self._tmp.name, poll_interval=0,
        )

    def _invoke(self, srv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["probe"], runner=self._runner(srv))
        return code, out.getvalue(), err.getvalue()

    def test_a_working_account_is_told_uploads_work(self):
        with FakeSandboxServer() as srv:
            code, out, err = self._invoke(srv)
        self.assertEqual(code, 0, err)
        self.assertIn("upload", out.lower())
        self.assertRegex(out, r"\d+(\.\d+)? (B|KiB|MiB)")
        # The rate is what makes the number actionable for sizing a real task.
        self.assertRegex(out, r"(MiB|kB|bytes)/s")
        # And the sandbox it made is gone.
        self.assertIn("DELETE /v1beta/environments/env_probe",
                      srv.method_paths())

    def test_a_blocked_window_is_named_as_the_window(self):
        with FakeSandboxServer(upload_status=400,
                               upload_body=FAIR_USE_BODY) as srv:
            code, out, err = self._invoke(srv)
        # Its own exit code, so a script can branch on "blocked" without
        # confusing it with a broken run (0 ok, 1 error, 2 usage, 3 expired).
        self.assertEqual(code, 4, out + err)
        text = out + err
        self.assertIn("fair use", text)
        self.assertIn("retry", text.lower())
        self.assertIn("DELETE /v1beta/environments/env_probe",
                      srv.method_paths())

    def test_the_probe_never_runs_a_task(self):
        """It answers a question about bytes; it must not spend interaction quota."""
        with FakeSandboxServer() as srv:
            self._invoke(srv)
        self.assertFalse([path for path in srv.method_paths()
                          if "/v1beta/interactions" in path],
                         srv.method_paths())

    def test_the_upload_is_small(self):
        with FakeSandboxServer() as srv:
            self._invoke(srv)
        self.assertGreater(srv.uploaded_bytes(), 0)
        self.assertLessEqual(srv.uploaded_bytes(), 2 * 1024 * 1024)

    def test_a_held_sandbox_is_reported(self):
        with FakeSandboxServer(delete_status=500) as srv:
            code, out, err = self._invoke(srv)
        self.assertIn("clean --yes", out + err)
        self.assertIn("DELETE /v1beta/environments/env_probe",
                      srv.method_paths())

    def test_probe_needs_no_arguments_and_no_key_of_its_own(self):
        # `probe` is a plain subcommand: no required positional, and the CLI
        # resolves the key the way every other network command does.
        args = cli.build_parser().parse_args(["probe"])
        self.assertEqual(args.command, "probe")


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


if __name__ == "__main__":
    unittest.main()
