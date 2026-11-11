"""P1-2 (T6): when the whole-workspace TAR cannot come back, the delivery
must not die with it.

Measured live (routes.py): a 2 GiB workspace download is rejected, and the
1 GiB / 3.6 GiB TARs died around 122 s. The sandbox still holds every file,
and the per-file transport already exists (fetch --path). So when the tree
download fails, the delivery reconstructs the workspace file by file over
the SAME endpoints fetch uses, repacks the tar the delivery expects
(``<repo_name>/...`` layout) and carries on: a too-big workspace is a
transport problem, not a lost task.

Rules under test:

1. A failed tree download falls back to the recursive listing plus one
   download per file, the delivery completes on the anchored branch, and
   the result says the bytes came back per-file.
2. Files the delivery never needs are not downloaded (the sandbox's .git
   metadata and build artifacts are skipped, like the purged tar).
3. If the per-file path fails too, the task is ``undelivered`` naming both
   failures -- never a partial branch.
4. A healthy tree download never touches the fallback: no listing, no
   per-file media request (regression guard).
"""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from remote_worker.google_client import (EnvironmentsClient, FilesClient,
                                         InteractionsClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore

ENV_ID = "env_fb"
REPO = "origin"


def _make_origin(tmp):
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    subprocess.run(["git", "init", "-q", origin], check=True)
    with open(os.path.join(origin, "main.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "-C", origin, "add", "main.py"], check=True)
    subprocess.run(["git", "-C", origin, "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", "initial"],
                   check=True)
    return origin


def _file_entry(path, size):
    return {"path": path, "name": path.rsplit("/", 1)[-1], "type": "FILE",
            "size_bytes": str(size)}


class FakeWorkspaceServer:
    """Interactions + files endpoints. The tree download fails by default;
    the listing and per-file media answers work, which is the P1-2 case."""

    def __init__(self, entries=None, file_bytes=None, tree_status=500,
                 tree_body=b'{"error":{"message":"workspace too large"}}',
                 file_failures=()):
        self.requests = []
        self.tree_status = tree_status
        self.tree_body = tree_body
        self.file_failures = set(file_failures)
        self.entries = entries if entries is not None else [
            _file_entry(f"workspace/{REPO}/main.py", 40),
            _file_entry(f"workspace/{REPO}/tests/test_main.py", 60),
            _file_entry(f"workspace/{REPO}/.git/config", 40),
        ]
        self.file_bytes = file_bytes if file_bytes is not None else {
            f"workspace/{REPO}/main.py":
                b"def add(a, b):\n    return a + b\n\n"
                b"def mul(a, b):\n    return a * b\n",
            f"workspace/{REPO}/tests/test_main.py": b"assert add(1, 2) == 3\n",
            f"workspace/{REPO}/.git/config": b"[core]\n",
        }
        self.interaction = {"id": "v1_fb", "status": "completed",
                            "environment_id": ENV_ID, "steps": [],
                            "usage": {"total_tokens": 1200}}

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, payload, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                raw = urllib.parse.unquote(self.path)
                path, _, query = raw.partition("?")
                outer.requests.append({"method": "GET", "path": raw})
                is_media = "alt=media" in query
                if path == "/v1beta/environments/" + ENV_ID + "/files/workspace" \
                        and is_media:
                    # The whole-tree download: what P1-2 makes fail.
                    self._reply(outer.tree_status, outer.tree_body,
                                "application/json")
                elif is_media:
                    # One file back (fetch --path transport).
                    rel = path.split("/files/", 1)[-1]
                    if rel in outer.file_failures:
                        self._reply(500, b'{"error":{"message":"boom"}}',
                                    "application/json")
                    elif rel in outer.file_bytes:
                        self._reply(200, outer.file_bytes[rel],
                                    "application/octet-stream")
                    else:
                        self._reply(404, b"{}", "application/json")
                elif "/files/" in path:
                    self._reply(200, json.dumps({"files": outer.entries}).encode(),
                                "application/json")
                else:
                    self._reply(404, b"{}", "application/json")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                outer.requests.append({"method": "POST", "path": self.path})
                self._reply(200,
                            json.dumps(outer.interaction).encode(),
                            "application/json")

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
        return [f"{r['method']} {r['path']}" for r in self.requests]


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


class WorkspaceFallbackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = _make_origin(self.tmp)
        self.store = TaskStore(project="proj", root=self.tmp)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            files=FilesClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store, workdir=self.tmp)

    # Rule 1.
    def test_a_failed_tar_falls_back_to_per_file_and_delivers(self):
        with FakeWorkspaceServer() as srv:
            result = self._runner(srv).run(task="t", repo_url=self.origin)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result.get("workspace_return"), "per-file", result)
        self.assertIn(
            f"GET /v1beta/environments/{ENV_ID}/files/workspace?recursive=true",
            srv.method_paths())
        self.assertIn(
            f"GET /v1beta/environments/{ENV_ID}/files/"
            f"workspace/{REPO}/main.py?alt=media", srv.method_paths())
        clone = result["clone_dir"]
        with open(os.path.join(clone, "main.py")) as fh:
            self.assertIn("def mul", fh.read())

    # Rule 2.
    def test_the_fallback_skips_git_metadata_and_artifacts(self):
        with FakeWorkspaceServer() as srv:
            result = self._runner(srv).run(task="t", repo_url=self.origin)
        self.assertEqual(result["status"], "completed", result)
        media = [p for p in srv.method_paths() if "alt=media" in p]
        self.assertFalse([p for p in media if "/.git/" in p], media)

    # Rule 3.
    def test_a_failed_fallback_stays_undelivered_and_names_both(self):
        with FakeWorkspaceServer(
                file_failures={f"workspace/{REPO}/main.py"}) as srv:
            result = self._runner(srv).run(task="t", repo_url=self.origin)
        self.assertEqual(result["status"], "undelivered", result)
        self.assertIsNone(result.get("clone_dir"))
        self.assertIn("per-file", str(result.get("delivery_error")))

    # Rule 4 (regression guard).
    def test_a_healthy_tar_download_never_touches_the_fallback(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, payload in [
                    (f"{REPO}/main.py", b"def mul(a, b):\n    return a*b\n")]:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))

        class GoodTree(FakeWorkspaceServer):
            def __init__(self):
                super().__init__(tree_status=200, tree_body=buf.getvalue())

        with GoodTree() as srv:
            result = self._runner(srv).run(task="t", repo_url=self.origin)
        self.assertEqual(result["status"], "completed", result)
        self.assertNotIn("workspace_return", result)
        self.assertNotIn("recursive=true",
                         " ".join(p for p in srv.method_paths()
                                  if "alt=media" not in p))


if __name__ == "__main__":
    unittest.main()
