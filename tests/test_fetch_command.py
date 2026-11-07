"""S13: the granularities the client already had are reachable from the CLI.

`run` always pulled the whole workspace as one tar, which is right for a task
and wrong for "what did it produce?" or "just give me that file": the whole tree
moves to answer either. `FilesClient.list_files` and single-file `download_to`
existed and nothing called them (odd/tasks/remote-worker-gaps.md L34) — the
defect of being implemented and buried.

Rules under test:

1. `fetch <task> --list` reads the workspace listing and downloads no file
   bytes; `--path` scopes the listing.
2. `fetch <task> --path FILE` brings that one file back and prints where it
   went; a directory as `--out` is a drop target, not a file to replace.
3. Reading costs no interaction quota: the bytes coming back are not a task.
4. Every failure is resolved before a request goes out, so a mistake costs
   nothing: no option, unknown task, a task that never had a sandbox.
5. An existing local file is never overwritten unasked — this is the one read
   that could damage the user's tree.
6. A sandbox that went away (7-day TTL) reads as expired with the action, not
   as a raw 404.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import mock

from remote_worker.google_client import (EnvironmentsClient, FilesClient,
                                         InteractionsClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from remote_worker import cli

ENVIRONMENT_ID = "env_fetch"


def _file_entry(path, size):
    return {"path": path, "sizeBytes": str(size)}

class FakeFilesServer:
    """The Environments files endpoints: listing, one file, the whole tree."""

    def __init__(self, entries=None, file_bytes=b"a = 1\n", get_status=200):
        self.requests = []
        self.entries = entries if entries is not None else [
            _file_entry("workspace/r/a.py", 12),
            _file_entry("workspace/r/README.md", 2048),
        ]
        self.file_bytes = file_bytes
        self.get_status = get_status
        self._server = None
        self._thread = None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, payload=b"", headers=None):
                self.send_response(code)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path,
                                       "body": b""})
                if outer.get_status >= 400:
                    self._reply(outer.get_status,
                                b'{"error":{"message":"not found"}}',
                                {"Content-Type": "application/json"})
                    return
                if "alt=media" in self.path:
                    self._reply(200, outer.file_bytes,
                                {"Content-Type": "application/octet-stream"})
                else:
                    self._reply(200, json.dumps({"files": outer.entries}).encode(),
                                {"Content-Type": "application/json"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                outer.requests.append({"method": "POST", "path": self.path,
                                       "body": self.rfile.read(length)})
                self._reply(200, json.dumps({"id": "v1_fetch",
                                             "steps": []}).encode(),
                            {"Content-Type": "application/json"})

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


class FetchCommandTest(unittest.TestCase):
    """Rules 4-6: the granularities the client already had are reachable."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store_root = os.path.join(self._tmp.name, "state")
        self.workdir = os.path.join(self._tmp.name, "work")

    def _store(self, record=None):
        store = TaskStore(project="proj", root=self.store_root)
        store.record(dict({"task_id": "t1", "status": "completed"},
                         **(record or {"environment_id": ENVIRONMENT_ID})))
        return store

    def _runner(self, srv, store):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=FilesClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            dispatcher=StubMemory(),
            store=store,
            workdir=self.workdir,
        )

    def _invoke(self, argv, srv, store):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, runner=self._runner(srv, store))
        return code, out.getvalue(), err.getvalue()

    # Rule 4.
    def test_list_reads_the_workspace_without_downloading_anything(self):
        with FakeFilesServer() as srv:
            code, out, err = self._invoke(["fetch", "t1", "--list"], srv,
                                          self._store())
        self.assertEqual(code, 0, err)
        self.assertIn(
            "GET /v1beta/environments/env_fetch/files/workspace?recursive=true",
            srv.method_paths())
        # The listing is what the user reads: paths, and enough to choose.
        self.assertIn("workspace/r/a.py", out)
        self.assertIn("workspace/r/README.md", out)
        self.assertIn("2", out)
        # Metadata only: not one byte of file content, no media request.
        self.assertNotIn("alt=media", " ".join(srv.method_paths()))

    def test_list_can_be_scoped_to_a_path(self):
        with FakeFilesServer() as srv:
            code, out, err = self._invoke(
                ["fetch", "t1", "--list", "--path", "workspace/r/docs"], srv,
                self._store())
        self.assertEqual(code, 0, err)
        self.assertIn("files/workspace/r/docs?recursive=true",
                      srv.method_paths()[0])

    def test_fetching_costs_no_interaction_quota(self):
        """Reading bytes back is not a task: no interaction is created."""
        with FakeFilesServer() as srv:
            self._invoke(["fetch", "t1", "--list"], srv, self._store())
        self.assertFalse([p for p in srv.method_paths()
                          if "/v1beta/interactions" in p], srv.method_paths())

    # Rule 5.
    def test_one_file_comes_down_and_the_path_is_printed(self):
        dest_dir = os.path.join(self._tmp.name, "out")
        dest = os.path.join(dest_dir, "a.py")
        with FakeFilesServer(file_bytes=b"A = 1\n") as srv:
            code, out, err = self._invoke(
                ["fetch", "t1", "--path", "workspace/r/a.py", "--out", dest],
                srv, self._store())
        self.assertEqual(code, 0, err)
        self.assertIn(
            "GET /v1beta/environments/env_fetch/files/workspace/r/a.py"
            "?alt=media", srv.method_paths())
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), b"A = 1\n")
        # The user is told where it landed and how big it is.
        self.assertIn(dest, out)
        self.assertRegex(out, r"\d+(\.\d+)? (B|KiB|MiB)")

    def test_the_default_destination_is_the_current_directory(self):
        cwd = os.path.join(self._tmp.name, "cwd")
        os.makedirs(cwd)
        with FakeFilesServer(file_bytes=b"A = 1\n") as srv:
            store = self._store()
            with mock.patch("os.getcwd", return_value=cwd):
                code, out, err = self._invoke(
                    ["fetch", "t1", "--path", "workspace/r/a.py"], srv, store)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(cwd, "a.py")),
                        os.listdir(cwd))

    def test_a_fetch_into_an_existing_directory_keeps_the_filename(self):
        target = os.path.join(self._tmp.name, "drop")
        os.makedirs(target)
        with FakeFilesServer(file_bytes=b"A = 1\n") as srv:
            code, out, err = self._invoke(
                ["fetch", "t1", "--path", "workspace/r/a.py", "--out", target],
                srv, self._store())
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(target, "a.py")),
                        os.listdir(target))

    def test_an_existing_local_file_is_not_overwritten_unasked(self):
        dest = os.path.join(self._tmp.name, "keep.py")
        with open(dest, "wb") as handle:
            handle.write(b"MINE\n")
        with FakeFilesServer(file_bytes=b"THEIRS\n") as srv:
            code, out, err = self._invoke(
                ["fetch", "t1", "--path", "workspace/r/a.py", "--out", dest],
                srv, self._store())
        self.assertNotEqual(code, 0)
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), b"MINE\n")
        self.assertIn("--out", out + err)
        self.assertNotIn("alt=media", " ".join(srv.method_paths()))

    def test_one_option_is_required_and_names_both(self):
        """The CLI prints its options, it never asks (S11)."""
        with FakeFilesServer() as srv:
            store = self._store()
            with self.assertRaises(SystemExit) as ctx:
                self._invoke(["fetch", "t1"], srv, store)
        self.assertEqual(ctx.exception.code, 2)

    def test_fetch_needs_a_task_id(self):
        self.assertRaises(SystemExit, cli.build_parser().parse_args, ["fetch"])

    def test_an_unknown_task_is_named_and_costs_no_request(self):
        with FakeFilesServer() as srv:
            store = self._store()
            code, out, err = self._invoke(["fetch", "nope", "--list"], srv,
                                          store)
        self.assertNotEqual(code, 0)
        self.assertIn("nope", out + err)
        self.assertEqual(srv.method_paths(), [])

    def test_a_task_that_never_had_a_sandbox_says_so(self):
        """`ask` tasks have no workspace to read; do not send a request for it."""
        with FakeFilesServer() as srv:
            store = self._store(record={"status": "completed"})
            code, out, err = self._invoke(["fetch", "t1", "--list"], srv,
                                          store)
        self.assertNotEqual(code, 0)
        self.assertIn("sandbox", out + err)
        self.assertEqual(srv.method_paths(), [])

    # Rule 6: the sandbox expiring is a known event, not a traceback.
    def test_an_expired_sandbox_reads_as_expired(self):
        with FakeFilesServer(get_status=404) as srv:
            code, out, err = self._invoke(["fetch", "t1", "--list"], srv,
                                          self._store())
        text = out + err
        self.assertEqual(code, 3, text)
        self.assertIn("expired", text)
        self.assertIn("7-day", text)
        # The action, not just the fact.
        self.assertIn("run", text.lower())
        self.assertNotIn("Traceback", text)

class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


if __name__ == "__main__":
    unittest.main()
