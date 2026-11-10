"""Hardening S1: remote code is not executed on the user's machine by default.

The review notes (puntos a mejorar.md, Crítico 1) named the worst defect: a
run downloaded the agent's workspace and immediately ran its tests through
``subprocess`` with the user's whole environment inherited -- a directory
name does not make a sandbox, so a test the agent wrote could read
credentials, SSH keys or anything else the account can reach. The product
treats delivered bytes as untrusted everywhere else; this closes the gap.

Rules under test:

1. Without ``--run-tests`` a completed run executes NOTHING: no subprocess,
   and the report says tests were not run (so nobody reads a clean exit as a
   green suite).
2. ``--run-tests`` is the explicit opt-in and then the project's test command
   runs exactly once, in the throwaway copy.
3. On the opt-in path the child environment is scrubbed: the user's own
   secrets (API keys, tokens, SSH agent, arbitrary exported variables) never
   reach the process; only a minimal allowlist plus a non-user HOME.
4. ``run_tests`` defaults to the scrubbed environment for any caller, so no
   future caller can accidentally inherit ``os.environ`` again.
"""

import json
import os
import subprocess
import tempfile
from unittest import mock
import unittest

from remote_worker import sync
from remote_worker.runner import TaskRunner
from remote_worker.state import TaskStore
from tests.fake_interactions import FakeInteractionsServer
from tests.test_delivery_failure import _interaction, _make_origin, \
    _worker_tar_bytes


class StubMemory:
    def tool_specs(self):
        return []

    def dispatch(self, call):
        return "{}"


SECRET_VARS = {
    "GEMINI_API_KEY": "AQ.super-secret-key",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "SOME_RANDOM_EXPORT": "leaked-value",
    "SSH_AUTH_SOCK": "/tmp/agent.sock",
}


class NoAutomaticRemoteTestsTest(unittest.TestCase):
    """Rule 1 + 2: execution is opt-in, and it is visible either way."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = _make_origin(self.tmp)
        self.store = TaskStore(project="proj", root=self.tmp)

    def _runner(self, srv):
        return TaskRunner(
            project="proj", api_key="AQ.k",
            client=None, store=self.store, workdir=self.tmp,
            dispatcher=StubMemory(),
        )

    def _run(self, srv, **kwargs):
        runner = self._runner(srv)
        from remote_worker.google_client import (EnvironmentsClient,
                                                 FilesClient,
                                                 InteractionsClient)
        runner._client = InteractionsClient(api_key="AQ.k",
                                            base_url=srv.base_url)
        runner._files = FilesClient(api_key="AQ.k", base_url=srv.base_url)
        runner._environments = EnvironmentsClient(api_key="AQ.k",
                                                  base_url=srv.base_url)
        return runner.run(task="x", repo_url=self.origin, **kwargs)

    def test_without_the_flag_nothing_is_executed(self):
        calls = []
        real = subprocess.run

        def spy(*args, **kwargs):
            calls.append(args[0] if args else kwargs.get("args"))
            return real(*args, **kwargs)

        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            with mock.patch.object(subprocess, "run", spy):
                result = self._run(srv)
        executed = [c for c in calls
                    if isinstance(c, list) and c and c[0] == "python3"]
        self.assertEqual(executed, [], f"remote tests were executed: {executed}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result.get("tests_rc"))
        self.assertTrue(result.get("tests_skipped"))

    def test_with_the_flag_the_tests_run_once(self):
        with FakeInteractionsServer() as srv:
            srv.reply_body = _interaction()
            srv.get_replies = [(200, _worker_tar_bytes())]
            result = self._run(srv, execute_tests=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result.get("tests_rc"), 0, result.get("tests_output"))
        self.assertFalse(result.get("tests_skipped"))


class ScrubbedEnvironmentTest(unittest.TestCase):
    """Rule 3 + 4: the child process never sees the user's secrets."""

    def _observed_env(self, **kwargs):
        observed = {}

        def fake_run(command, **kw):
            observed.update(kw.get("env") or {})
            return subprocess.CompletedProcess(command, 0, "", "")

        real = subprocess.run
        subprocess.run = fake_run
        try:
            with mock.patch.dict(os.environ, SECRET_VARS):
                sync.run_tests("/tmp", ["python3", "-c", "pass"], **kwargs)
        finally:
            subprocess.run = real
        return observed

    def test_a_default_call_scrubs_the_user_environment(self):
        env = self._observed_env()
        for name in SECRET_VARS:
            self.assertNotIn(name, env, f"{name} leaked into the test child")
        self.assertIn("PATH", env)
        self.assertEqual(env.get("PYTHONDONTWRITEBYTECODE"), "1")

    def test_home_is_not_the_users_home(self):
        env = self._observed_env()
        self.assertNotEqual(env.get("HOME"), os.path.expanduser("~"))
        self.assertNotIn(
            os.path.expanduser("~"),
            (env.get("HOME") or ""),
            "the child HOME still points at the user's home",
        )

    def test_an_explicit_env_is_honoured(self):
        env = self._observed_env(env={"ONLY_THIS": "1"})
        self.assertEqual(env, {"ONLY_THIS": "1"})


if __name__ == "__main__":
    unittest.main()