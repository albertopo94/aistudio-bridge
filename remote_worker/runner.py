"""Glue: one task, start to finish.

Composes the tested pieces — planner, google_client, engram_adapter,
function_loop, sync, state — into the single operation the CLI exposes.
Kept thin on purpose: every decision lives in its own module.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from urllib.parse import urlsplit

from .engram_adapter import EngramMemory, MemoryDispatcher
from .function_loop import run_until_complete
from .google_client import FilesClient, InteractionsClient
from .planner import agent_config, worker_contract
from .state import DEFAULT_ROOT, TaskStore, _slug
from .sync import apply_to_branch, mount_repo, run_tests

AGENT = "antigravity-preview-09-2026"
DEFAULT_TEST_COMMAND = ["python3", "tests/test_main.py"]


def _repo_name(url: str) -> str:
    tail = urlsplit(url).path.rstrip("/").split("/")[-1] or "repo"
    return re.sub(r"\.git$", "", tail) or "repo"


class TaskRunner:
    """Runs a delegated task and brings the result back."""

    def __init__(self, *, project: str, api_key: str,
                 client=None, files=None, dispatcher=None, store=None,
                 workdir: str | None = None,
                 memory_enabled: bool = True):
        self.project = project
        # Clients are created on first use: reading local task state must not
        # need an API key.
        self._api_key = api_key
        self._client = client
        self._files = files
        self.store = store or TaskStore(project=project)
        # Stable across sessions so a recorded task can be resumed later;
        # the process temp directory would not survive a restart.
        self.workdir = workdir or os.path.join(
            DEFAULT_ROOT, _slug(project), "work"
        )
        self.dispatcher = dispatcher or MemoryDispatcher(
            EngramMemory(project=project)
        )
        self.memory_enabled = memory_enabled

    @property
    def client(self):
        if self._client is None:
            self._client = InteractionsClient(api_key=self._api_key)
        return self._client

    @property
    def files(self):
        if self._files is None:
            self._files = FilesClient(api_key=self._api_key)
        return self._files

    def run(self, *, task: str, repo_url: str, model: str | None = None,
            budget: int | None = None,
            branch: str | None = None) -> dict:
        config = agent_config(model=model, budget=budget)
        repo_name = _repo_name(repo_url)
        repo_dir = f"/workspace/{repo_name}"
        task_id = f"t_{int(time.time())}"
        branch = branch or f"worker/{repo_name}-{int(time.time())}"

        tools = [{"type": "code_execution"}]
        if self.memory_enabled:
            tools.extend(self.dispatcher.tool_specs())

        interaction = self.client.create_interaction(
            agent=AGENT,
            input=task,
            environment={
                "type": "remote",
                "sources": [mount_repo(repo_url, repo_dir)],
            },
            agent_config=config,
            system_instruction=worker_contract(
                project=self.project, repo_dir=repo_dir,
                memory_tools=self.memory_enabled,
            ),
            tools=tools,
        )
        self.store.record({
            "task_id": task_id,
            "status": interaction.get("status"),
            "interaction_id": interaction.get("id"),
            "environment_id": interaction.get("environment_id"),
            "repo_url": repo_url,
            "branch": branch,
            "model": config["model"],
            "budget": config["max_total_tokens"],
            "task": task,
        })

        final = run_until_complete(self.client, self.dispatcher, interaction)
        status = final.get("status")
        tokens = self.client.usage(final).get("total_tokens")
        self.store.record({
            "task_id": task_id,
            "status": status,
            "interaction_id": final.get("id"),
            "environment_id": final.get("environment_id"),
            "tokens": tokens,
            "report": self.client.last_model_output(final),
        })

        result = {
            "task_id": task_id,
            "status": status,
            "tokens": tokens,
            "budget": config["max_total_tokens"],
            "branch": branch,
            "report": self.client.last_model_output(final),
        }
        if status != "completed":
            return result  # incomplete / failed: nothing to bring back

        environment_id = final.get("environment_id")
        os.makedirs(self.workdir, exist_ok=True)
        tar_path = os.path.join(self.workdir, f"{task_id}.tar")
        self.files.download_to(environment_id, "workspace", tar_path)

        applied = apply_to_branch(
            tar_path=tar_path, repo_url=repo_url, branch=branch,
            workdir=os.path.join(self.workdir, "clone"),
            repo_dir=repo_name,
        )
        # The diff the user reviews must not contain artifacts that the test
        # run will add, so the tests run against a throwaway copy.
        tests_clone = self._prepare_tests_clone(applied["clone_dir"])
        tests_rc, tests_output = run_tests(tests_clone, DEFAULT_TEST_COMMAND)
        result.update({
            "clone_dir": applied["clone_dir"],
            "changed": applied["changed"],
            "diff": applied["diff"],
            "tests_rc": tests_rc,
            "tests_output": tests_output,
        })
        self.store.record({
            "task_id": task_id,
            "branch": branch,
            "clone_dir": applied["clone_dir"],
            "changed": applied["changed"],
            "tests_rc": tests_rc,
        })
        return result

    def _prepare_tests_clone(self, clone_dir: str) -> str:
        """A fresh copy of the clone to run the tests in.

        A previous run may have left a stale clone.tests; it is removed
        first so the copy is always clean and never merged with leftovers.
        """
        tests_clone = clone_dir + ".tests"
        shutil.rmtree(tests_clone, ignore_errors=True)
        shutil.copytree(clone_dir, tests_clone,
                        ignore=shutil.ignore_patterns(".git"))
        return tests_clone

    def continue_task(self, *, task_id: str,
                      budget: int | None = None) -> dict:
        record = self.store.get(task_id)
        if record.get("status") != "incomplete":
            raise ValueError(
                f"task {task_id} is {record.get('status')!r}; "
                "only an incomplete task can continue"
            )
        config = agent_config(model=record.get("model"), budget=budget)
        interaction = self.client.create_interaction(
            agent=AGENT,
            input="continue",
            environment=record.get("environment_id"),
            previous_interaction_id=record.get("interaction_id"),
            agent_config=config,
        )
        final = run_until_complete(self.client, self.dispatcher, interaction)
        status = final.get("status")
        tokens = self.client.usage(final).get("total_tokens")
        self.store.record({
            "task_id": task_id,
            "status": status,
            "interaction_id": final.get("id"),
            "tokens": tokens,
            "report": self.client.last_model_output(final),
        })
        return {
            "task_id": task_id,
            "status": status,
            "tokens": tokens,
            "budget": config["max_total_tokens"],
            "branch": record.get("branch"),
            "report": self.client.last_model_output(final),
        }

    def status(self, task_id: str | None = None):
        if task_id is None:
            return self.store.list()
        return self.store.get(task_id)

    def cancel(self, task_id: str) -> dict:
        record = self.store.get(task_id)
        updated = self.store.record({
            "task_id": task_id,
            "status": "canceled",
            "note": "canceled locally; the Google interaction may still finish",
        })
        return {**record, **updated}