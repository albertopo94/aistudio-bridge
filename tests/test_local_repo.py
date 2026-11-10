"""G4: delegate against a LOCAL repository by uploading its working tree.

A repository that only lives on this machine, or one too big for the 500 MB
`repository` source, cannot be cloned by the sandbox — it has no route to the
user's disk. The alternative is the resumable upload the service documents:
archive the tree, send it into the sandbox, and unpack it there.

Rules under test:

1. The archive carries what the user sees in `git status`: tracked files plus
   untracked-but-not-ignored ones. Ignored build output and `.git` stay home.
2. A directory that is not a git repository is refused with the reason, before
   anything is sent.
3. No local size guard exists: the ceiling is the account's fair-use window,
   so the tree is sent and the service decides (see tests/test_routes.py).
4. The uploaded archive is unpacked with `extract=true` into
   `workspace/<name>/`, which is the same layout a mounted repository gets, so
   the return path keeps working unchanged.
5. The interaction then runs inside that environment (the id as a bare
   string, the documented "reuse" form), not in a fresh one: the archive
   is already there, and the service requires `type` on an
   EnvironmentConfig object.
   fresh sandbox, or the uploaded files would be invisible to it.
6. The CLI accepts exactly one of `--repo` and `--local-repo`.
"""

import json
import os
import subprocess
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import mock

from remote_worker.google_client import (EnvironmentsClient,
                                        InteractionsClient, UploadClient)
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from remote_worker import sync
from remote_worker import cli


class FakeSandboxServer:
    """The three surfaces G4 touches: env create, upload session, interaction."""

    def __init__(self):
        self.requests = []
        self.interaction = {
            "id": "v1_up", "object": "interaction", "status": "failed",
            "environment_id": None, "usage": {"total_tokens": 7}, "steps": [],
        }
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
                outer.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v
                                for k, v in self.headers.items()},
                    "body": body,
                })

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
                if self.path.startswith("/v1beta/environments"):
                    self._reply(200, json.dumps(
                        {"id": "env_up", "status": "active"}).encode(),
                        {"Content-Type": "application/json"})
                elif self.path.startswith("/v1beta/interactions"):
                    self._reply(200, json.dumps(outer.interaction).encode(),
                                {"Content-Type": "application/json"})
                else:
                    self._reply(404, b'{"error":"no route"}')

            def do_PUT(self):
                self._record()
                if "uploadType=resumable" in self.path:
                    self._reply(200, b"", {
                        "Location": f"http://{self.headers['Host']}/session"})
                elif self.path.startswith("/session"):
                    # Speak the protocol: every non-final chunk answers 308
                    # with the byte the server committed; only the final one
                    # returns the file envelope.
                    content_range = self.headers.get("Content-Range", "")
                    try:
                        span, total = content_range.split(" ")[1].split("/")
                        end, total = int(span.split("-")[1]), int(total)
                    except (ValueError, IndexError):
                        end, total = 0, 0
                    if total and end == total - 1:
                        self._reply(200, json.dumps(
                            {"files": [{"path": "workspace/r/a.py"}]}
                        ).encode(), {"Content-Type": "application/json"})
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

    def paths(self):
        return [request["path"] for request in self.requests]


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


def _git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                   text=True, check=True)


def _make_repo(root):
    repo = os.path.join(root, "r")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("x = 1\n")
    with open(os.path.join(repo, "new.txt"), "w") as handle:
        handle.write("untracked\n")
    with open(os.path.join(repo, "build.log"), "w") as handle:
        handle.write("noise\n")
    with open(os.path.join(repo, ".gitignore"), "w") as handle:
        handle.write("*.log\n")
    _git(repo, "add", "a.py", ".gitignore")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=t",
         "commit", "-qm", "init")
    return repo


class ArchiveRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_archive_keeps_tracked_and_untracked_but_not_ignored(self):
        repo = _make_repo(self._tmp.name)
        dest = os.path.join(self._tmp.name, "out.tar.gz")
        info = sync.archive_repo(repo, dest)
        import tarfile
        with tarfile.open(dest) as tar:
            names = sorted(tar.getnames())
        self.assertIn("a.py", names)
        self.assertIn("new.txt", names)
        self.assertNotIn("build.log", names)
        self.assertFalse([n for n in names if n.startswith(".git/")], names)
        self.assertEqual(info["bytes"], os.path.getsize(dest))
        self.assertEqual(info["files"], len(names))

    def test_archive_records_the_base_commit_of_the_snapshot(self):
        # P0-1: the delivery must be built against the exact point Antigravity
        # received, so archive_repo names the HEAD it packed. Without this the
        # branch that advanced during the task would absorb a delivery built
        # over an older snapshot.
        repo = _make_repo(self._tmp.name)
        dest = os.path.join(self._tmp.name, "out.tar.gz")
        info = sync.archive_repo(repo, dest)
        proc = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True)
        self.assertEqual(info["base_commit"], proc.stdout.strip())

    def test_archive_refuses_when_the_repo_has_no_commit(self):
        # A repo with files but no commit cannot name a base point.
        plain = os.path.join(self._tmp.name, "unborn")
        os.makedirs(plain)
        with open(os.path.join(plain, "a.py"), "w") as fh:
            fh.write("x = 1\n")
        _git(plain, "init", "-q")
        _git(plain, "add", "a.py")
        with self.assertRaises(Exception):
            sync.archive_repo(plain, os.path.join(self._tmp.name, "x.tar.gz"))

    def test_a_plain_directory_is_refused_before_anything_is_sent(self):
        plain = os.path.join(self._tmp.name, "plain")
        os.makedirs(plain)
        with self.assertRaises(Exception):
            sync.archive_repo(plain, os.path.join(self._tmp.name, "x.tar.gz"))


class RemoteHeadTest(unittest.TestCase):
    """P0-1: the remote HEAD a mounted repository would hand the sandbox is
    captured for the task record, and an unreachable repo yields None."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "origin")
        os.makedirs(self.repo)
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True)
        with open(os.path.join(self.repo, "main.py"), "w") as fh:
            fh.write("x = 1\n")
        subprocess.run(["git", "-C", self.repo, "add", "main.py"],
                       check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t",
                        "-c", "user.email=t@t", "commit", "-qm",
                        "initial"], check=True)

    def test_remote_head_is_captured_for_a_reachable_repo(self):
        from remote_worker import runner as runner_module
        head = subprocess.run(["git", "-C", self.repo, "rev-parse",
                               "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        captured = runner_module._remote_head(self.repo)
        self.assertEqual(captured, head)

    def test_remote_head_is_none_for_an_unreachable_repo(self):
        from remote_worker import runner as runner_module
        self.assertIsNone(
            runner_module._remote_head("http://127.0.0.1:1/nope/repo.git")
        )


class LocalRepoRunTest(unittest.TestCase):
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

    def test_upload_extracts_into_workspace_and_runs_there(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer() as srv:
            result = self._runner(srv).run(
                task="x", local_repo=repo, allow_domains=["example.com"])
            paths = srv.paths()
            create = next(r for r in srv.requests
                          if r["method"] == "POST"
                          and r["path"].startswith("/v1beta/environments"))
            init = next(r for r in srv.requests
                        if "uploadType=resumable" in r["path"])
            interaction = next(r for r in srv.requests
                               if r["path"].startswith("/v1beta/interactions"))
        self.assertIn("/v1beta/environments", paths[0])
        self.assertIn("extract=true", init["path"])
        self.assertIn("/files/workspace/r/", init["path"])
        self.assertIn("example.com", create["body"].decode())
        self.assertEqual(json.loads(interaction["body"])["environment"],
                         "env_up")
        self.assertEqual(result["environment_id"], "env_up")
        self.assertGreater(result["archive_bytes"], 0)

    def test_the_result_is_cloned_from_the_local_path(self):
        repo = _make_repo(self._tmp.name)
        with FakeSandboxServer() as srv:
            runner = self._runner(srv)
            seen = {}

            def fake_apply(**kwargs):
                seen.update(kwargs)
                return {"clone_dir": "c", "diff": "", "changed": []}

            srv.interaction = dict(srv.interaction, status="completed")
            with mock.patch("remote_worker.runner.apply_to_branch", fake_apply), \
                    mock.patch.object(runner, "_prepare_tests_clone",
                                      lambda clone: clone), \
                    mock.patch("remote_worker.runner.run_tests",
                               lambda *a, **k: (0, "")), \
                    mock.patch.object(type(runner.files), "download_to",
                                      lambda *a, **k: None):
                runner.run(task="x", local_repo=repo)
        self.assertEqual(seen["repo_url"], os.path.abspath(repo))
        self.assertEqual(seen["repo_dir"], "r")

    def test_the_delivery_is_anchored_on_the_snapshot_base_commit(self):
        # P0-1: the base_commit that run() captured from the archive must reach
        # apply_to_branch, so the delivery is anchored on the exact point the
        # sandbox received -- not the remote line that may advance meanwhile.
        repo = _make_repo(self._tmp.name)
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()
        with FakeSandboxServer() as srv:
            runner = self._runner(srv)
            seen = {}

            def fake_apply(**kwargs):
                seen.update(kwargs)
                return {"clone_dir": "c", "diff": "", "changed": []}

            srv.interaction = dict(srv.interaction, status="completed")
            with mock.patch("remote_worker.runner.apply_to_branch", fake_apply), \
                    mock.patch.object(runner, "_prepare_tests_clone",
                                      lambda clone: clone), \
                    mock.patch("remote_worker.runner.run_tests",
                               lambda *a, **k: (0, "")), \
                    mock.patch.object(type(runner.files), "download_to",
                                      lambda *a, **k: None):
                runner.run(task="x", local_repo=repo)
        self.assertEqual(seen["base_commit"], head)


class CliTest(unittest.TestCase):
    def test_exactly_one_source_is_required(self):
        parser = cli.build_parser()
        for argv in (["run", "x"],
                     ["run", "x", "--repo", "https://g.com/o/r",
                      "--local-repo", "/tmp/r"]):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_local_repo_is_accepted_alone(self):
        args = cli.build_parser().parse_args(["run", "x", "--local-repo", "."])
        self.assertEqual(args.local_repo, ".")
        self.assertIsNone(args.repo)


if __name__ == "__main__":
    unittest.main()