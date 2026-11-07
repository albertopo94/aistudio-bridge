"""G1 + G2: put a fence around the sandbox, and be able to clean it up.

Both gaps are the data boundary the README Security section promises.

G1 — network allowlist. The sandbox has unrestricted egress by default, so a
task can read /workspace and POST it anywhere (the docs' own hooks discussion
makes this concrete for third-party repos: G10). The service supports an
allowlist on the create call itself; the product just never exposed it.
Shape from the API reference: ``environment.network.allowlist`` is a list of
rules, each ``{"domain": ..., "transform"?: {...}, "credential"?: ...}``;
``"*.example.com"`` does NOT match the root domain; the string ``"disabled"``
means no egress at all.

Rules under test:
1. Without the flag the request carries no ``network`` field at all — the
   default behaviour stays byte-identical.
2. ``--allow-domain`` builds ``{"allowlist": [{"domain": ...}]}`` in order,
   de-duplicated, and warns when a ``*.x`` entry has no bare ``x`` companion,
   because the wildcard does not match the root.
3. ``--no-network`` sends the ``"disabled"`` form and cannot be combined with
   ``--allow-domain``.
4. Continuations reference the environment **by id**, so the fence is set once
   at creation and carries through every round trip: a continuation must send
   the id (never a fresh unrestricted environment) and must not redeclare the
   network. If a continuation ever sent `"remote"` again, the task would slip
   out of the fence halfway through.
5. A bad domain (spaces, slash, empty) is a usage error, not a request.

G2 — environments cleanup. Nothing ever deleted an environment; they sat for
their 7-day TTL holding the mounted repo. The Environments API offers
list/get/delete.

Rules under test:
6. ``list_environments`` GETs /v1beta/environments with page_size/page_token
   and returns the environments plus the next-page token.
7. ``delete_environment`` DELETEs /v1beta/environments/{id}; an empty id is a
   ValueError.
8. ``clean`` DRY-RUNS by default (deleting is destructive): it lists what would
   go and deletes nothing; ``--yes`` deletes.
9. ``clean`` never touches an environment belonging to a task that is not
   finished — active work is protected even with ``--yes``.
10. F2: the service lists environments per ACCOUNT, so rule 9 must hold
    across every project this tool tracks, not only the one the CLI was
    run from: an unfinished task in another project still owns a live
    sandbox, and ``clean --yes`` from a different project must not delete
    it.
"""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
import io

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker import cli
    from remote_worker.google_client import InteractionsClient, EnvironmentsClient
    from remote_worker.runner import TaskRunner
    from remote_worker.state import TaskStore
    from remote_worker.planner import network_config
except ImportError:
    cli = None


def _env(eid, status="active"):
    return {"id": eid, "status": status, "created": "2026-11-01T00:00:00Z",
            "last_accessed": "2026-11-05T00:00:00Z", "file_count": "12",
            "size_bytes": "4096"}


def _interaction(status="completed", iid="v1", env="env_1"):
    return {"id": iid, "object": "interaction", "status": status,
            "environment_id": env, "usage": {"total_tokens": 5}, "steps": []}


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


class NetworkConfigTest(unittest.TestCase):
    def setUp(self):
        if network_config is None:
            self.fail("RED: planner.network_config missing")

    # Rule 2
    def test_allowlist_shape_and_order(self):
        cfg = network_config(allow_domains=["pypi.org", "github.com"])
        self.assertEqual(cfg, {"allowlist": [{"domain": "pypi.org"},
                                             {"domain": "github.com"}]})

    def test_duplicates_collapse(self):
        cfg = network_config(allow_domains=["a.com", "a.com"])
        self.assertEqual(cfg, {"allowlist": [{"domain": "a.com"}]})

    # Rule 5
    def test_bad_domains_are_rejected(self):
        for bad in ["", " ", "has space", "a.com/path", "http://a.com"]:
            with self.assertRaises(ValueError, msg=bad):
                network_config(allow_domains=[bad])

    # Rule 3
    def test_disabled_form(self):
        self.assertEqual(network_config(no_network=True),
                         {"allowlist": "disabled"})

    def test_nothing_requested_means_no_config(self):
        self.assertIsNone(network_config())


class AllowlistWireTest(unittest.TestCase):
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

    # Rule 1
    def test_default_sends_no_network_field(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("failed")
            self._runner(srv).run(task="x",
                                  repo_url="https://github.com/o/r")
            body = json.loads(srv.requests[0]["body"])
        self.assertNotIn("network", json.dumps(body))

    # Rule 2 on the wire
    def test_allow_domains_reach_the_environment(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("failed")
            self._runner(srv).run(
                task="x", repo_url="https://github.com/o/r",
                allow_domains=["pypi.org"],
            )
            env = json.loads(srv.requests[0]["body"])["environment"]
        self.assertEqual(env["network"],
                         {"allowlist": [{"domain": "pypi.org"}]})
        self.assertEqual(env["sources"][0]["target"], "/workspace/r")

    # Rule 4: the fence is set once and the continuations stay in it by id.
    def test_continuations_stay_in_the_fenced_environment(self):
        call = {"type": "function_call", "name": "memory_search",
                "id": "c1", "arguments": {}}
        with FakeInteractionsServer() as srv:
            srv.replies = [
                (200, _interaction("requires_action", iid="v1",
                                   env="env_fenced")),
                (200, _interaction("failed", iid="v2", env="env_fenced")),
            ]
            srv.replies[0] = (200, dict(srv.replies[0][1], steps=[call]))
            self._runner(srv).run(task="x", repo_url="https://github.com/o/r",
                                  allow_domains=["pypi.org"])
            bodies = [json.loads(r["body"]) for r in srv.requests]
        self.assertEqual(len(bodies), 2)
        # The create declared the fence on a fresh environment object.
        self.assertEqual(bodies[0]["environment"]["network"],
                         {"allowlist": [{"domain": "pypi.org"}]})
        # The continuation must rejoin that same environment by id, and must
        # not declare a new unrestricted one.
        self.assertEqual(bodies[1]["environment"], "env_fenced")
        self.assertNotIn("network", json.dumps(bodies[1]))

    # Rule 3 on the wire
    def test_no_network_sends_the_disabled_form(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction("failed")
            self._runner(srv).run(task="x", repo_url="https://github.com/o/r",
                                  no_network=True)
            env = json.loads(srv.requests[0]["body"])["environment"]
        self.assertEqual(env["network"], {"allowlist": "disabled"})

    # Rule 5 on the wire: the CLI refuses before any request.
    def test_cli_validates_domains_before_any_request(self):
        with FakeInteractionsServer() as srv:
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(["run", "x", "--repo", "u",
                              "--allow-domain", "not a domain"],
                             runner=self._runner(srv))
            requests = list(srv.requests)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(requests, [])

    def test_cli_refuses_allow_domain_with_no_network(self):
        class NoCall:
            def run(self, **kw):
                raise AssertionError("the guard must stop before any call")

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["run", "x", "--repo", "u", "--no-network",
                          "--allow-domain", "a.com"], runner=NoCall())
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("comb", err.getvalue().lower())


class EnvironmentsClientTest(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker missing")

    def _client(self, srv):
        return EnvironmentsClient(api_key="AQ.k", base_url=srv.base_url)

    # Rule 6
    def test_list_environments(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = json.dumps(
                {"environments": [_env("e1"), _env("e2")],
                 "next_page_token": "tok"}).encode()
            page = self._client(srv).list_environments()
            req = srv.last_request
        self.assertEqual(req["method"], "GET")
        self.assertTrue(req["path"].startswith("/v1beta/environments"))
        self.assertEqual([e["id"] for e in page["environments"]], ["e1", "e2"])
        self.assertEqual(page["next_page_token"], "tok")

    def test_list_paginates(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = json.dumps(
                {"environments": [_env("e3")]}).encode()
            self._client(srv).list_environments(page_token="tok2",
                                                page_size=500)
            req = srv.last_request
        self.assertIn("page_token=tok2", req["path"])
        self.assertIn("page_size=500", req["path"])

    # Rule 7
    def test_delete_environment(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = b"{}"
            self._client(srv).delete_environment("e9")
            req = srv.last_request
        self.assertEqual(req["method"], "DELETE")
        self.assertEqual(req["path"], "/v1beta/environments/e9")

    def test_delete_requires_an_id(self):
        with FakeInteractionsServer() as srv:
            with self.assertRaises(ValueError):
                self._client(srv).delete_environment("")


class CleanCommandTest(unittest.TestCase):
    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = TaskStore(project="proj", root=self._tmp.name)
        self.store.record({"task_id": "t_done", "status": "completed",
                           "environment_id": "env_done"})
        self.store.record({"task_id": "t_running", "status": "incomplete",
                           "environment_id": "env_running"})

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            environments=self._client(srv),
            dispatcher=StubMemory(), store=self.store,
            workdir=self._tmp.name,
        )

    def _client(self, srv):
        return EnvironmentsClient(api_key="AQ.k", base_url=srv.base_url)

    def _server_list(self, srv, ids):
        srv.reply_bytes = json.dumps(
            {"environments": [_env(i) for i in ids]}).encode()

    # Rule 8: dry-run is the default; deleting needs --yes.
    def test_clean_dry_runs_by_default(self):
        with FakeInteractionsServer() as srv:
            self._server_list(srv, ["env_done", "stray_env"])
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["clean"], runner=self._runner(srv))
            methods = [r["method"] for r in srv.requests]
        printed = out.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(methods, ["GET"])          # nothing deleted
        self.assertIn("env_done", printed)
        self.assertIn("stray_env", printed)
        self.assertIn("--yes", printed)             # how to actually do it

    # Rule 8: --yes deletes the finished ones.
    def test_clean_yes_deletes_finished_environments(self):
        with FakeInteractionsServer() as srv:
            self._server_list(srv, ["env_done", "stray_env"])
            srv.delete_replies = [(200, b"{}"), (200, b"{}")]
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["clean", "--yes"], runner=self._runner(srv))
            deletes = [r["path"] for r in srv.requests if r["method"] ==
                       "DELETE"]
        self.assertEqual(code, 0)
        self.assertEqual(deletes, ["/v1beta/environments/env_done",
                                   "/v1beta/environments/stray_env"])

    # Rule 9: the active task's environment is protected even with --yes.
    def test_clean_protects_active_work(self):
        with FakeInteractionsServer() as srv:
            self._server_list(srv, ["env_done", "env_running"])
            srv.delete_replies = [(200, b"{}")]
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                cli.main(["clean", "--yes"], runner=self._runner(srv))
            deletes = [r["path"] for r in srv.requests if r["method"] ==
                       "DELETE"]
        self.assertEqual(deletes, ["/v1beta/environments/env_done"])
        self.assertIn("env_running", out.getvalue())   # named as kept

    # A delete failure stops the loop with the truth, not a traceback.
    def test_clean_reports_a_failed_delete(self):
        with FakeInteractionsServer() as srv:
            self._server_list(srv, ["env_done"])
            srv.delete_replies = [(403, json.dumps(
                {"error": {"message": "forbidden"}}).encode())]
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["clean", "--yes"], runner=self._runner(srv))
        combined = out.getvalue() + err.getvalue()
        self.assertNotEqual(code, 0)
        self.assertIn("env_done", combined)
        self.assertNotIn("Traceback", combined)


class CleanCrossProjectProtectionTest(unittest.TestCase):
    """F2: environments are account-scoped on the service side, so the
    protection must span every local project store, not just the one the
    CLI is run from."""

    def setUp(self):
        if cli is None:
            self.fail("RED: remote_worker missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # The CLI runs from project "alpha"...
        self.store = TaskStore(project="alpha", root=self._tmp.name)
        self.store.record({"task_id": "t_a_done", "status": "completed",
                           "environment_id": "env_a_done"})
        # ...but project "beta" has unfinished work in env_beta_active.
        other = TaskStore(project="beta", root=self._tmp.name)
        other.record({"task_id": "t_b_active", "status": "requires_action",
                      "environment_id": "env_beta_active"})

    def _runner(self, srv):
        return TaskRunner(
            project="alpha", api_key="AQ.k",
            client=InteractionsClient(api_key="AQ.k", base_url=srv.base_url),
            environments=EnvironmentsClient(api_key="AQ.k",
                                            base_url=srv.base_url),
            dispatcher=StubMemory(), store=self.store,
            workdir=self._tmp.name,
        )

    # Rule 10: --yes from project alpha must spare beta's live sandbox.
    def test_clean_protects_unfinished_work_in_other_projects(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = json.dumps(
                {"environments": [
                    _env("env_a_done"), _env("env_beta_active"),
                    _env("stray_env"),
                ]}).encode()
            srv.delete_replies = [(200, b"{}"), (200, b"{}")]
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["clean", "--yes"], runner=self._runner(srv))
            deletes = [r["path"] for r in srv.requests
                       if r["method"] == "DELETE"]
        self.assertEqual(code, 0)
        # Finished work in this project + the stray go; the other
        # project's unfinished work stays.
        self.assertEqual(deletes, ["/v1beta/environments/env_a_done",
                                   "/v1beta/environments/stray_env"])
        self.assertNotIn("/v1beta/environments/env_beta_active", deletes)
        self.assertIn("env_beta_active", out.getvalue())

    # Rule 10 also covers the dry run: the kept list is where the user sees
    # the protection, so it must name the other project's active sandbox
    # and must not offer it for deletion.
    def test_clean_dry_run_keeps_other_projects_active_env(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = json.dumps(
                {"environments": [_env("env_a_done"),
                                  _env("env_beta_active")]}).encode()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["clean"], runner=self._runner(srv))
            methods = [r["method"] for r in srv.requests]
        self.assertEqual(code, 0)
        self.assertEqual(methods, ["GET"])
        lines = out.getvalue().splitlines()
        self.assertIn("would delete: env_a_done", lines)
        self.assertNotIn("would delete: env_beta_active", lines)
        self.assertIn("kept (a task is still using it): env_beta_active",
                      lines)


if __name__ == "__main__":
    unittest.main()
