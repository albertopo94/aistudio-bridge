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
from .function_loop import (RUNNING_STATUSES, WatchLimitExceeded,
                            run_until_complete, wait_until_done)
from .google_client import (EnvironmentsClient, FilesClient,
                            InteractionsClient, InteractionsError,
                            InteractionsTimeoutError)
from .planner import agent_config, network_config, worker_contract
from .state import DEFAULT_ROOT, TaskStore, _slug
from .sync import apply_to_branch, mount_repo, run_tests

AGENT = "antigravity-preview-09-2026"
DEFAULT_TEST_COMMAND = ["python3", "tests/test_main.py"]


def _expired_report(record: dict) -> str:
    """Why a resumed task is unrecoverable, in the user's words.

    A 404 on a resumed task means the sandbox went away. Google's
    documented lifecycle is 7 days, so that is the number we name; the
    per-task budget is echoed only when the record has one, never invented.
    """
    budget = record.get("budget")
    suffix = f" (it stopped at the {budget}-token budget)" if budget else ""
    return (
        "the sandbox expired (7-day TTL); this task cannot be resumed"
        f"{suffix}. Start a new run with the same repo and task."
    )


def _repo_name(url: str) -> str:
    tail = urlsplit(url).path.rstrip("/").split("/")[-1] or "repo"
    return re.sub(r"\.git$", "", tail) or "repo"


class TaskRunner:
    """Runs a delegated task and brings the result back."""

    def __init__(self, *, project: str, api_key: str,
                 client=None, files=None, environments=None,
                 dispatcher=None, store=None,
                 workdir: str | None = None,
                 memory_enabled: bool = True,
                 poll_interval: float = 5.0, max_polls: int = 360):
        self.project = project
        # Clients are created on first use: reading local task state must not
        # need an API key.
        self._api_key = api_key
        self._client = client
        self._files = files
        self._environments = environments
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
        # The watch of a background interaction (G13): 5s apart, 30 minutes
        # of reads by default, matching the service's own polling examples.
        self.poll_interval = poll_interval
        self.max_polls = max_polls

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

    @property
    def environments(self):
        if self._environments is None:
            self._environments = EnvironmentsClient(api_key=self._api_key)
        return self._environments

    def run(self, *, task: str, repo_url: str, model: str | None = None,
            budget: int | None = None,
            branch: str | None = None,
            background: bool = False,
            allow_domains=None, no_network: bool = False) -> dict:
        config = agent_config(model=model, budget=budget)
        network = network_config(allow_domains=allow_domains,
                                 no_network=no_network)
        repo_name = _repo_name(repo_url)
        repo_dir = f"/workspace/{repo_name}"
        task_id = f"t_{int(time.time())}"
        branch = branch or f"worker/{repo_name}-{int(time.time())}"

        tools = [{"type": "code_execution"}]
        if self.memory_enabled:
            tools.extend(self.dispatcher.tool_specs())
        contract = worker_contract(
            project=self.project, repo_dir=repo_dir,
            memory_tools=self.memory_enabled,
        )

        environment = {
            "type": "remote",
            "sources": [mount_repo(repo_url, repo_dir)],
        }
        if network:
            # The fence is declared once here; every later turn rejoins this
            # environment by id, so it holds for the whole task.
            environment["network"] = network
        interaction = self.client.create_interaction(
            agent=AGENT,
            input=task,
            environment=environment,
            agent_config=config,
            system_instruction=contract,
            tools=tools,
            background=background,
        )
        self.store.record({
            "task_id": task_id,
            "kind": "run",
            "status": interaction.get("status"),
            "interaction_id": interaction.get("id"),
            "environment_id": interaction.get("environment_id"),
            "repo_url": repo_url,
            "repo_dir": repo_dir,
            "branch": branch,
            "model": config["model"],
            "budget": config["max_total_tokens"],
            "task": task,
        })

        final = run_until_complete(self.client, self.dispatcher, interaction,
                                   system_instruction=contract, tools=tools)
        if background:
            try:
                final = wait_until_done(
                    self.client, self.dispatcher, final,
                    poll_interval=self.poll_interval,
                    max_polls=self.max_polls,
                    system_instruction=contract, tools=tools,
                )
            except WatchLimitExceeded as exc:
                # The watch ended, not the task: the interaction may still
                # complete on its own. Keep it resumable and say so.
                report = (f"still running when our watch ended ({exc}); "
                          "the task is not lost — check with "
                          f"'remote-worker status {task_id} --live' or "
                          f"resume watching with 'remote-worker continue "
                          f"{task_id}'")
                self.store.record({"task_id": task_id,
                                   "status": "incomplete",
                                   "report": report})
                return {
                    "task_id": task_id, "status": "incomplete",
                    "tokens": None, "budget": config["max_total_tokens"],
                    "branch": branch, "report": report, "reason": "watch",
                }
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
        # G13: do not race a running interaction. Chaining to an interaction
        # the service still reports as running is a documented 400, and a
        # retry of it would fork a second branch of the conversation. Ask
        # first, refuse with the honest instruction, send nothing.
        # A record without an interaction id (or a machine without a key)
        # cannot be checked and keeps the old behaviour.
        if record.get("interaction_id") and self._api_key:
            try:
                current = self.client.get_interaction(record["interaction_id"])
            except InteractionsError as exc:
                if exc.status != 404:
                    raise
                # The conversation itself is gone (free tier keeps one for a
                # day). Same verdict as a dead sandbox, found one step earlier.
                report = _expired_report(record)
                self.store.record({"task_id": task_id,
                                   "status": "expired",
                                   "report": report})
                return {
                    "task_id": task_id, "status": "expired", "tokens": None,
                    "budget": None, "branch": record.get("branch"),
                    "report": report,
                }
            if current.get("status") in RUNNING_STATUSES:
                report = (f"the interaction is still running (status "
                          f"{current.get('status')!r}); continuing now would "
                          "be rejected by the service. Check with "
                          f"'remote-worker status {task_id} --live', or stop "
                          f"it with 'remote-worker cancel {task_id}'")
                return {
                    "task_id": task_id,
                    "status": "incomplete",
                    "tokens": self.client.usage(current).get("total_tokens"),
                    "budget": None,
                    "branch": record.get("branch"),
                    "report": report,
                    "reason": "running",
                }
        # G15: system_instruction and tools are interaction-scoped, so this
        # continuation must re-send them. Rebuild them from the task record:
        # run tasks get the repo contract and the code tools, ask tasks get
        # the research contract and the research tools. A record without
        # repo_dir (created before this fix) falls back to the repo_url, and
        # a record that has neither keeps the ask-style contract.
        if record.get("kind") == "run" or (
                record.get("kind") is None and record.get("repo_url")):
            repo_dir = record.get("repo_dir")
            if not repo_dir and record.get("repo_url"):
                repo_dir = f"/workspace/{_repo_name(record['repo_url'])}"
            repo_dir = repo_dir or "(no repository mounted)"
            tools = [{"type": "code_execution"}]
        else:
            repo_dir = "(no repository mounted)"
            tools = [{"type": "code_execution"}, {"type": "google_search"},
                     {"type": "url_context"}]
        if self.memory_enabled:
            tools.extend(self.dispatcher.tool_specs())
        contract = worker_contract(
            project=self.project, repo_dir=repo_dir,
            memory_tools=self.memory_enabled,
        )
        try:
            interaction = self.client.create_interaction(
                agent=AGENT,
                input="continue",
                environment=record.get("environment_id"),
                previous_interaction_id=record.get("interaction_id"),
                agent_config=config,
                system_instruction=contract,
                tools=tools,
            )
            final = run_until_complete(self.client, self.dispatcher,
                                       interaction,
                                       system_instruction=contract,
                                       tools=tools)
        except InteractionsError as exc:
            # G9: a 404 here means the sandbox is gone (7-day TTL) or the
            # conversation id no longer resolves. Either way the task is
            # unrecoverable, and the raw API body tells the user neither
            # that nor that retrying is useless. One attempt, no retry.
            if exc.status != 404:
                raise
            report = _expired_report(record)
            self.store.record({
                "task_id": task_id,
                "status": "expired",
                "report": report,
            })
            return {
                "task_id": task_id,
                "status": "expired",
                "tokens": None,
                "budget": None,
                "branch": record.get("branch"),
                "report": report,
            }
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

    # Statuses after which an environment is certainly no longer needed.
    FINISHED_STATUSES = ("completed", "canceled", "expired", "failed")

    def clean(self, *, apply: bool = False) -> dict:
        """List (and optionally delete) sandboxes this account still holds.

        Nothing else ever removes an environment: a finished task leaves its
        sandbox -- and the repository mounted inside it -- alive for the rest
        of the 7-day TTL. Deletion is irreversible and the service has no
        per-project scope (these are the project's environments on the API
        side, not this tool's), so the default is a dry run and work that is
        not finished is never touched.
        """
        ids, token = [], None
        # Bounded: a service that kept handing back the same token must not
        # turn a cleanup listing into an endless loop.
        for _page in range(20):
            page = self.environments.list_environments(page_token=token)
            ids.extend(env.get("id") for env in page.get("environments") or []
                       if env.get("id"))
            token = page.get("next_page_token")
            if not token:
                break
        else:
            raise ValueError(
                "too many pages of environments to list; clean up in "
                "smaller scopes"
            )

        in_use = {
            task.get("environment_id")
            for task in self.store.list()
            if task.get("environment_id")
            and task.get("status") not in self.FINISHED_STATUSES
        }
        kept = [i for i in ids if i in in_use]
        candidates = [i for i in ids if i not in in_use]
        if not apply:
            return {"applied": False, "deleted": [], "failed": [],
                    "kept_active": kept, "candidates": candidates}

        deleted, failed = [], []
        for environment_id in candidates:
            try:
                self.environments.delete_environment(environment_id)
                deleted.append(environment_id)
            except InteractionsError as exc:
                # Stop on the first refusal: a 403 here usually means
                # something systemic, and continuing would only produce noise.
                failed.append({"environment_id": environment_id,
                               "error": str(exc)})
                break
        return {"applied": True, "deleted": deleted, "failed": failed,
                "kept_active": kept, "candidates": candidates}

    def status(self, task_id: str | None = None):
        if task_id is None:
            return self.store.list()
        return self.store.get(task_id)

    def cancel(self, task_id: str) -> dict:
        record = self.store.get(task_id)
        # The local record is always canceled: that is the part this tool owns.
        # Stopping the remote work is best-effort on top of it, because the
        # service only cancels a running interaction and does not document
        # what it says about one that already finished (G12). A failure there
        # is reported, never raised, and never undoes the local cancel.
        interaction_id = record.get("interaction_id")
        remote_error = None
        if interaction_id and self._api_key:
            try:
                self.client.cancel_interaction(interaction_id)
            except (InteractionsError, InteractionsTimeoutError) as exc:
                remote_error = str(exc)
        updated = self.store.record({
            "task_id": task_id,
            "status": "canceled",
            "note": ("canceled through the service" if not remote_error
                     else "canceled locally; the service refused the cancel"
                     f": {remote_error}"),
        })
        return {
            **record,
            **updated,
            "remote_canceled": bool(interaction_id and self._api_key
                                    and not remote_error),
            "remote_error": remote_error,
        }

    def live_status(self, task_id: str) -> dict:
        """Ask the service what it thinks of a recorded task (G18).

        Local state says what we last saw; only the service knows whether the
        work went on after this process stopped watching. ``error`` carries a
        readable reason when the service has no such interaction any more.
        """
        record = self.store.get(task_id)
        interaction_id = record.get("interaction_id")
        if not interaction_id:
            return {"record": record, "interaction": None,
                    "error": "this task has no interaction recorded, so the "
                             "service cannot be asked"}
        try:
            interaction = self.client.get_interaction(interaction_id)
        except InteractionsError as exc:
            if exc.status == 404:
                return {
                    "record": record,
                    "interaction": None,
                    "error": f"the service no longer has interaction "
                             f"{interaction_id} (its retention ran out); the "
                             f"local record is stale",
                }
            raise
        return {"record": record, "interaction": interaction, "error": None}

    def ask(self, task: str, *, model: str | None = None,
            budget: int | None = None, project: str | None = None,
            stream: bool = False, background: bool = False) -> dict:
        """Delegate a NON-code task and return the worker's text.

        Unlike :meth:`run`, ask does NOT mount a repository and does NOT
        download a workspace: it is a pure text round trip. The worker may
        still use Google Search, URL context, code execution and the memory
        tools. This is the mode for "use Google for whatever I want" —
        research, analysis, generation — and for offloading token cost from
        the local model to the (free) Gemini tier.
        """
        config = agent_config(model=model, budget=budget)
        proj = project or self.project
        task_id = f"t_{int(time.time())}"

        tools = [{"type": "code_execution"}, {"type": "google_search"},
                 {"type": "url_context"}]
        if self.memory_enabled:
            tools.extend(self.dispatcher.tool_specs())
        contract = worker_contract(
            project=proj, repo_dir="(no repository mounted)",
            memory_tools=self.memory_enabled,
        )

        if stream:
            interaction = self.client.stream_interaction(
                agent=AGENT,
                input=task,
                environment="remote",
                agent_config=config,
                system_instruction=contract,
                tools=tools,
                on_delta=lambda t: print(t, end="", flush=True),
            )
        else:
            interaction = self.client.create_interaction(
                agent=AGENT,
                input=task,
                environment="remote",
                agent_config=config,
                system_instruction=contract,
                tools=tools,
                background=background,
            )
        self.store.record({
            "task_id": task_id, "kind": "ask",
            "status": interaction.get("status"),
            "interaction_id": interaction.get("id"),
            "environment_id": interaction.get("environment_id"),
            "model": config["model"], "budget": config["max_total_tokens"],
            "task": task,
        })

        final = run_until_complete(self.client, self.dispatcher, interaction,
                                   system_instruction=contract, tools=tools)
        if background:
            try:
                final = wait_until_done(
                    self.client, self.dispatcher, final,
                    poll_interval=self.poll_interval,
                    max_polls=self.max_polls,
                    system_instruction=contract, tools=tools,
                )
            except WatchLimitExceeded as exc:
                report = (f"still running when our watch ended ({exc}); the "
                          "answer is not lost — check with "
                          f"'remote-worker status {task_id} --live'")
                self.store.record({"task_id": task_id,
                                   "status": "incomplete",
                                   "report": report})
                return {"task_id": task_id, "status": "incomplete",
                        "tokens": None,
                        "budget": config["max_total_tokens"],
                        "text": report, "reason": "watch"}
        status = final.get("status")
        tokens = self.client.usage(final).get("total_tokens")
        text = self.client.last_model_output(final)
        self.store.record({
            "task_id": task_id, "status": status, "tokens": tokens,
            "interaction_id": final.get("id"),
            "environment_id": final.get("environment_id"),
            "text": text,
        })
        return {
            "task_id": task_id, "status": status, "tokens": tokens,
            "budget": config["max_total_tokens"], "text": text,
        }
