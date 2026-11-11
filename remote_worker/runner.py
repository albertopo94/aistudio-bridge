"""Glue: one task, start to finish.

Composes the tested pieces — planner, google_client, engram_adapter,
function_loop, sync, state — into the single operation the CLI exposes.
Kept thin on purpose: every decision lives in its own module.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit

from .engram_adapter import EngramMemory, MemoryDispatcher
from .function_loop import (RUNNING_STATUSES, WatchLimitExceeded,
                            run_until_complete, wait_until_done)
from .google_client import (EnvironmentsClient, FilesClient,
                            InteractionsClient, InteractionsError,
                            InteractionsTimeoutError, UploadClient, UploadError)
from .planner import (agent_config, network_config, task_parts,
                      worker_contract)
from .routes import (human_duration, human_rate, is_size_limit,
                     upload_failure_message)
from .state import (DEFAULT_ROOT, TaskStore, _slug, list_project_stores,
                    new_task_id)
from .sync import (EXCLUDED_DIRS, GitError, apply_to_branch, archive_repo,
                   inline_source, mount_gcs, mount_repo, run_tests,
                   INLINE_TOTAL_LIMIT_BYTES)

AGENT = "antigravity-preview-09-2026"
DEFAULT_TEST_COMMAND = ["python3", "tests/test_main.py"]
AGENTS_TARGET = "/.agents/AGENTS.md"

#: What :meth:`TaskRunner.probe` uploads: small enough to cost about a second
#: at the service's own rate, big enough that a zero-byte fluke cannot pass
#: for a working window.
PROBE_BYTES = 1024 * 1024
#: A name no real task file would take, so the probe cannot collide with a
#: mounted repository in a reused sandbox.
PROBE_TARGET = "workspace/.remote-worker-probe.bin"


def _upload_progress():
    """An ``on_progress`` callback that says where a long upload stands.

    The service uploads at ~1.1-1.6 MiB/s whatever your own line does, so 500
    MB takes about six minutes and 1.5 GiB about sixteen. Silence that long is
    indistinguishable from a hang, so each acknowledged chunk is reported with
    the rate measured from this upload (not a remembered average) and the ETA
    that rate implies.

    Bytes the service has not acknowledged are never claimed: an upload the
    account's fair-use window refuses halfway ends at the line for what did
    get through.
    """
    started = time.monotonic()

    def report(sent: int, total: int) -> None:
        elapsed = time.monotonic() - started
        rate = sent / elapsed if (elapsed > 0 and sent) else 0.0
        head = f"upload {sent}/{total} bytes at {human_rate(rate)}"
        if sent >= total or rate <= 0:
            print(f"{head} done in {human_duration(elapsed)}",
                  file=sys.stderr, flush=True)
        else:
            eta = (total - sent) / rate
            print(f"{head} eta {human_duration(eta)}",
                  file=sys.stderr, flush=True)

    return report



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


class _LocalCallCache:
    """D-1: persist answered local calls so a resumed task never re-runs one.

    The crash window: :func:`run_until_complete` dispatches a call (a side
    effect like ``memory_save`` persists), THEN creates the interaction that
    carries the result. A process killed between the two leaves the record
    pointing at the ``requires_action`` interaction; ``continue_task`` re-reads
    it, the same ``call_id`` is pending again, and the side effect ran twice.

    The answer is stored in the task record under
    ``local_calls`` -> ``<interaction_id>`` -> ``<call_id>`` right after the
    side effect, keyed by the interaction and the call's own id (a call id is
    only unique within its interaction). ``run_until_complete`` consults this
    before dispatch and writes after it, so a call that raised is never cached
    and a fresh retry re-executes it. A call replayed from the cache is a
    return path, not a side effect.

    The cache reads and writes through the same locked ``TaskStore.record`` as
    everything else, so it is safe under concurrency and survives across
    processes.
    """

    def __init__(self, store: TaskStore, task_id: str):
        self._store = store
        self._task_id = task_id

    def get(self, interaction_id: str, call_id: str):
        record = self._store.get(self._task_id)
        return (record.get("local_calls") or {}).get(interaction_id, {}).get(
            call_id)

    def set(self, interaction_id: str, call_id: str, answer: str) -> None:
        record = self._store.get(self._task_id)
        local = record.get("local_calls") or {}
        local.setdefault(interaction_id, {})[call_id] = answer
        self._store.record({
            "task_id": self._task_id, "local_calls": local
        })


def _gcs_name(uri: str) -> str:
    """The target name for a gcs source: the last non-empty path segment.

    ``gs://bkt/tree`` -> ``tree``, ``gs://bkt`` -> ``bkt`` (the bucket name
    when the prefix is empty). The bucket itself is already validated
    non-empty by :func:`mount_gcs`.
    """
    parts = [p for p in uri[len("gs://"):].split("/") if p]
    return parts[-1] if parts else ""


def _remote_head(repo_url: str) -> str | None:
    """The remote commit a mounted repository will hand the sandbox.

    The sandbox clones ``repo_url`` on its own (the ``repository`` source),
    so the exact point it receives is the remote HEAD at mount time. Capture
    that with ``git ls-remote`` just before the task starts: it is the
    closest honest proxy for the snapshot identity (P0-1) without cloning
    locally. None when the repo is unreachable from this machine (e.g. a
    host that only the sandbox can reach) -- the task then runs without a
    pinned base and keeps the pre-P0-1 behaviour.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    line = proc.stdout.strip()
    if not line:
        return None
    return line.split()[0] or None


class TaskRunner:
    """Runs a delegated task and brings the result back."""

    def __init__(self, *, project: str, api_key: str,
                 client=None, files=None, environments=None,
                 uploads=None,
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
        self._uploads = uploads
        self.store = store or TaskStore(project=project)
        # Stable across sessions so a recorded task can be resumed later;
        # the process temp directory would not survive a restart.
        self.workdir = workdir or os.path.join(
            DEFAULT_ROOT, _slug(project), "work"
        )
        # The dispatcher is the default when no memory is available at all
        # (``memory_enabled=False``) or a caller-supplied double. A real task
        # builds its OWN dispatcher per run/continue/ask (P0-2) so one task's
        # ``--memory-save`` permission can never bleed into another's.
        self._memory = EngramMemory(project=project)
        self.dispatcher = dispatcher or MemoryDispatcher(self._memory)
        self.memory_enabled = memory_enabled
        # The watch of a background interaction (G13): 5s apart, 30 minutes
        # of reads by default, matching the service's own polling examples.
        self.poll_interval = poll_interval
        self.max_polls = max_polls

    def _task_dispatcher(self, memory_save: bool):
        """The dispatcher for one task, with that task's memory permission.

        P0-2: memory permission must be immutable and per-task, so a task
        can never change another's. A real (built-in) dispatcher is created
        fresh per task from the project's memory with the task's
        ``--memory-save`` flag; a caller-supplied double is already
        task-scoped and is passed through untouched. Returns None when
        memory is disabled, in which case the memory tools are not even
        advertised.
        """
        if not self.memory_enabled:
            return None
        if self.dispatcher is not None and not isinstance(
                self.dispatcher, MemoryDispatcher):
            return self.dispatcher
        return MemoryDispatcher(self._memory, allow_save=memory_save)

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

    @property
    def uploads(self):
        if self._uploads is None:
            self._uploads = UploadClient(api_key=self._api_key)
        return self._uploads

    def _upload_local_repo(self, local_repo: str, repo_name: str, *,
                           sources: list, network, task_id: str) -> dict:
        """Send a local working tree into a fresh sandbox (G4).

        The tree is archived and uploaded with ``extract=true`` into
        ``/workspace/<name>/``, the layout a mounted repository would have
        given the sandbox, so the return path is the same either way.

        Nothing here refuses the archive for being large. The ceiling is the
        account's fair-use window over a time span, which no local constant
        can know (see :mod:`remote_worker.routes`), so the service is asked and
        its refusal is translated instead.
        """
        os.makedirs(self.workdir, exist_ok=True)
        archive_path = os.path.join(self.workdir, f"{task_id}.tar.gz")
        environment_id = None
        try:
            archive = archive_repo(local_repo, archive_path)
            environment = self.environments.create_environment(
                sources=sources, network=network,
            )
            environment_id = environment.get("id")
            if not environment_id:
                raise ValueError("the service did not return an environment id")
            try:
                self.uploads.upload_file(
                    environment_id=environment_id,
                    path=f"workspace/{repo_name}/",
                    file_path=archive_path,
                    extract=True,
                    on_progress=_upload_progress(),
                )
            except UploadError as exc:
                # A sandbox holding a half-uploaded archive is of no use to
                # anyone, and it would sit there for the 7-day TTL. Release the
                # one this attempt made, then explain the refusal.
                released = self._release_environment(environment_id)
                raise ValueError(
                    upload_failure_message(exc.status, exc.body, environment_id,
                                           released=released)
                ) from exc
        finally:
            # The archive is as big as the repository. Leaving it behind would
            # quietly cost the user a second copy of their tree per task.
            if os.path.exists(archive_path):
                os.remove(archive_path)
        return {"id": environment_id, "archive_bytes": archive["bytes"],
                "archive_files": archive["files"],
                "base_commit": archive["base_commit"]}

    def _release_environment(self, environment_id: str) -> bool:
        """Best-effort delete of a sandbox this attempt made and cannot use."""
        try:
            self.environments.delete_environment(environment_id)
        except Exception:
            return False
        return True

    def _gcs_token(self, gcs_token_file: str | None) -> str | None:
        """The bearer token a private GCS bucket needs, or None (public path).

        Resolved in order: the explicit file the user named, else ``gcloud
        auth print-access-token``. A missing or empty file is a ValueError
        that names the file -- the user pointed at a token and got none. A
        gcloud that times out, fails, or prints nothing is the public-bucket
        path and is silently treated as "no token": the request proceeds and
        the service answers if the bucket is public.

        The token lives only in memory here; it is never logged, printed, or
        stored in the task record (AGENTS.md §2).
        """
        if gcs_token_file:
            try:
                with open(gcs_token_file, encoding="utf-8") as fh:
                    value = fh.read().strip()
            except OSError as exc:
                raise ValueError(
                    f"cannot read the GCS token file {gcs_token_file}: "
                    f"{exc.strerror}"
                ) from exc
            if not value:
                raise ValueError(
                    f"the GCS token file {gcs_token_file} is empty"
                )
            return value
        if not shutil.which("gcloud"):
            return None
        try:
            proc = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return proc.stdout.strip()

    def _deliver(self, *, task_id, branch, environment_id, repo_url,
                 repo_name, result, execute_tests, delete_env=False,
                 base_commit=None):
        """Bring a completed task's workspace home (shared by run/continue).

        Delivery is the one step of a completed task that can still fail, and
        it failed for real: the download of a 3.6 GiB workspace answered 500
        after the agent had spent 93k tokens (L39). The bytes were still in
        the sandbox; the task record said `completed` and the raw error
        escaped. So the return path is taken as a unit with a stated outcome:
        either the work lands on a branch, or the task says `undelivered` and
        names how to recover it. A half-written archive is never left behind.

        Hardening S3: this is the one delivery operation, called by both
        :meth:`run` and :meth:`continue_task`, so a continuation that
        completes stops being the path that never came home.

        P1-2: when the whole-tree download fails (the measured ceiling),
        the delivery does not die with it -- it reconstructs the same tar
        file by file over the transport ``fetch --path`` uses, and the
        result says the bytes came back per-file. Only when BOTH roads
        fail does the task stay ``undelivered``, naming both failures.
        """
        os.makedirs(self.workdir, exist_ok=True)
        tar_path = os.path.join(self.workdir, f"{task_id}.tar")
        workspace_return = None
        try:
            try:
                self.files.download_to(environment_id, "workspace", tar_path)
            except (InteractionsError, InteractionsTimeoutError) as tar_exc:
                try:
                    self._rebuild_workspace_tar(environment_id, repo_name,
                                                tar_path)
                except Exception as file_exc:  # noqa: BLE001 - both named
                    raise RuntimeError(
                        f"the workspace tar download failed ({tar_exc}) and "
                        f"the per-file fallback failed ({file_exc})"
                    ) from file_exc
                workspace_return = "per-file"
            applied = apply_to_branch(
                tar_path=tar_path, repo_url=repo_url, branch=branch,
                # P0-2: each task applies into its OWN clone. A shared
                # work/clone let two concurrent tasks race on the same
                # directory (re-clone, branch reset, _drop_removed) and the
                # delivery of one could land on the other's tree.
                workdir=os.path.join(self.workdir, task_id, "clone"),
                repo_dir=repo_name, base_commit=base_commit,
            )
        except (InteractionsError, InteractionsTimeoutError, GitError,
                tarfile.TarError, OSError, RuntimeError) as exc:
            return self._undelivered(task_id, branch=branch,
                                     environment_id=environment_id,
                                     result=result, exc=exc)
        finally:
            # The workspace archive is the whole sandbox tree. Nothing after
            # this reads it, so it goes now rather than accumulating one copy
            # per task -- including the partial one a failed download leaves.
            if os.path.exists(tar_path):
                os.remove(tar_path)
        if workspace_return:
            # The branch the user is about to review traveled a different
            # road than the default; saying so beats a silent transport swap.
            result["workspace_return"] = workspace_return
        # Hardening S1: the delivered tree was written by a prompted agent.
        # Running its tests is executing that code on the user's machine, so
        # it never happens unless the user says so with --run-tests; the
        # report says tests were not run, so a clean exit is never read as a
        # green suite.
        if execute_tests:
            # The diff the user reviews must not contain artifacts that the
            # test run will add, so the tests run against a throwaway copy.
            tests_clone = self._prepare_tests_clone(applied["clone_dir"])
            tests_rc, tests_output = run_tests(
                tests_clone, DEFAULT_TEST_COMMAND)
            result.update({
                "clone_dir": applied["clone_dir"],
                "changed": applied["changed"],
                "diff": applied["diff"],
                "base_moved": applied.get("base_moved", False),
                "tests_rc": tests_rc,
                "tests_output": tests_output,
            })
            self.store.record({
                "task_id": task_id,
                "branch": branch,
                "clone_dir": applied["clone_dir"],
                "changed": applied["changed"],
                "base_moved": applied.get("base_moved", False),
                "tests_rc": tests_rc,
            })
        else:
            result.update({
                "clone_dir": applied["clone_dir"],
                "changed": applied["changed"],
                "diff": applied["diff"],
                "base_moved": applied.get("base_moved", False),
                "tests_rc": None,
                "tests_skipped": True,
            })
            self.store.record({
                "task_id": task_id,
                "branch": branch,
                "clone_dir": applied["clone_dir"],
                "changed": applied["changed"],
                "base_moved": applied.get("base_moved", False),
                "tests_skipped": True,
            })
        if delete_env:
            # The delivery is verified at this point (the branch exists), so
            # the sandbox is a redundant copy and releasing it early is safe
            # (T25/L42). A failed delete must not turn a delivered run into a
            # failure: the outcome is the delivery, and the error is named.
            try:
                self.environments.delete_environment(environment_id)
                result["env_released"] = True
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                result["env_released"] = False
                result["env_release_error"] = str(exc)
        return result

    def run(self, *, task: str, repo_url: str | None = None,
            model: str | None = None,
            budget: int | None = None,
            branch: str | None = None,
            background: bool = False,
            allow_domains=None, no_network: bool = False,
            reuse_env: str | None = None,
            agents_file: str | None = None,
            mount_files=None,
            images=None,
            local_repo: str | None = None,
            delete_env: bool = False,
            gcs_source: str | None = None,
            gcs_token_file: str | None = None,
            memory_save: bool = False,
            execute_tests: bool = False) -> dict:
        config = agent_config(model=model, budget=budget)
        # P0-2: a task-scoped dispatcher carries THIS task's memory
        # permission; the shared self.dispatcher is never mutated.
        dispatcher = self._task_dispatcher(memory_save)
        if gcs_source is not None:
            # P1-1: the gcs source route is disabled until it has a working
            # git return path. A gcs tree is not a git repository, so a
            # completed run would call apply_to_branch() with repo_url
            # "gs://..." and fail a git clone, leaving the task undelivered
            # with no honest return. Refuse up front with a clear message
            # instead of offering a route that cannot come back.
            raise ValueError(
                "--gcs-source is disabled: a Cloud Storage tree is not a git "
                "repository, so this route has no working return path to a "
                "local branch yet. Use --repo or --local-repo, or re-enable "
                "GCS once the git return path is built."
            )
        if gcs_source is not None and local_repo is not None:
            raise ValueError("pass either --gcs-source or --local-repo, not both")
        if gcs_source is not None and repo_url is not None:
            raise ValueError("pass either --gcs-source or --repo, not both")
        if local_repo is None and repo_url is None and gcs_source is None:
            raise ValueError(
                "one of repo_url, --local-repo or --gcs-source is required"
            )
        if reuse_env is not None and not reuse_env.strip():
            raise ValueError("--reuse-env needs an environment id")
        if local_repo is not None:
            # A local tree is not reachable from the sandbox, so it travels as
            # an uploaded archive. Its own path is what the result is cloned
            # from afterwards, so git truth never leaves the machine.
            repo_url = os.path.abspath(local_repo)
            repo_name = _repo_name(os.path.basename(repo_url.rstrip("/")))
        elif gcs_source is not None:
            # The gs:// URI is what `status` should show for where the code
            # came from; the sandbox COPIES the tree out of the bucket at
            # mount time (docs/agent-environment), it is not a clone.
            repo_url = gcs_source
            repo_name = _gcs_name(gcs_source)
        else:
            repo_name = _repo_name(repo_url)
        repo_dir = f"/workspace/{repo_name}"
        # A private bucket is read through the sandbox's egress proxy, which
        # injects the user's own OAuth bearer on the wire (docs/agent-
        # environment "Private sources"). The token comes from the file the
        # user named, or from `gcloud auth print-access-token`; it lives only
        # in memory here and is never printed or stored in the task record.
        network = network_config(allow_domains=allow_domains,
                                 no_network=no_network)
        # A GCS bearer is only meaningful when the source is the user's own
        # bucket; a repository or local-repo run must not inherit one, so the
        # token is resolved only on the gcs path.
        token = self._gcs_token(gcs_token_file) if gcs_source is not None else None
        if token is not None:
            # The documented rule for a private bucket: an allowlist entry
            # that injects the bearer on storage.googleapis.com. With no user
            # fence the docs add {"domain": "*"} as the catch-all; with a
            # fence the gcs rule joins the user's rules and the fence stays
            # a fence (no wildcard added).
            gcs_rule = {
                "domain": "storage.googleapis.com",
                "transform": {"Authorization": f"Bearer {token}"},
            }
            if no_network:
                raise ValueError(
                    "--gcs-source with a token cannot be combined with "
                    "--no-network"
                )
            if network is None:
                network = {"allowlist": [gcs_rule, {"domain": "*"}]}
            else:
                network["allowlist"].append(gcs_rule)
        task_id = new_task_id()
        branch = branch or f"worker/{repo_name}-{task_id}"

        tools = [{"type": "code_execution"}]
        if dispatcher is not None:
            tools.extend(dispatcher.tool_specs())
        contract = worker_contract(
            project=self.project, repo_dir=repo_dir,
            memory_tools=self.memory_enabled,
        )

        if gcs_source is not None:
            sources = [mount_gcs(gcs_source, repo_dir)]
        elif local_repo is not None:
            sources = []
        else:
            sources = [mount_repo(repo_url, repo_dir)]
        sources.extend(self._inline_sources(agents_file=agents_file,
                                            mount_files=mount_files))
        upload_info = None
        if local_repo is not None:
            upload_info = self._upload_local_repo(
                local_repo, repo_name, sources=sources, network=network,
                task_id=task_id,
            )
            # The archive already lives in a sandbox that was created with
            # its sources and network, so the interaction references it the
            # documented way: the id as a bare string. A {"id": ...} object
            # is an EnvironmentConfig missing `type` and the service rejects
            # it (T25 live defect; the reference makes `type` required).
            environment = upload_info["id"]
        elif reuse_env:
            # An existing sandbox is named by id; the mounts this task needs
            # travel as the documented EnvironmentConfig form, where
            # environment_id means "update this environment, do not create a
            # new one" and `type` is required.
            environment = {"type": "remote", "environment_id": reuse_env,
                           "sources": sources}
        else:
            environment = {"type": "remote", "sources": sources}
            if network:
                # The fence is declared once here; every later turn rejoins
                # this environment by id, so it holds for the whole task.
                environment["network"] = network
        interaction = self.client.create_interaction(
            agent=AGENT,
            input=task_parts(task, images),
            environment=environment,
            agent_config=config,
            system_instruction=contract,
            tools=tools,
            background=background,
        )
        # P0-1: name the exact snapshot this task sent, so the delivery can be
        # anchored on it instead of the remote line that may advance while the
        # agent works. local-repo names the HEAD it packed; a remote repository
        # names the HEAD the sandbox is about to mount; a gcs source has no
        # git base (it is a bucket, not a repo) and stays unpinned.
        if local_repo is not None:
            base_commit = upload_info["base_commit"]
        elif repo_url is not None:
            base_commit = _remote_head(repo_url)
        else:
            base_commit = None
        self.store.record(create=True, task={
            "task_id": task_id,
            "kind": "run",
            "status": interaction.get("status"),
            "interaction_id": interaction.get("id"),
            "environment_id": interaction.get("environment_id"),
            "repo_url": repo_url,
            "repo_dir": repo_dir,
            "base_commit": base_commit,
            "branch": branch,
            "model": config["model"],
            "budget": config["max_total_tokens"],
            "task": task,
        })

        final = run_until_complete(self.client, dispatcher, interaction,
                                   system_instruction=contract, tools=tools,
                                   on_round=self._round_persister(task_id),
                                   call_cache=_LocalCallCache(self.store,
                                                              task_id))
        if background:
            try:
                final = wait_until_done(
                    self.client, dispatcher, final,
                    poll_interval=self.poll_interval,
                    max_polls=self.max_polls,
                    system_instruction=contract, tools=tools,
                    on_round=self._round_persister(task_id),
                    call_cache=_LocalCallCache(self.store, task_id),
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
            "environment_id": final.get("environment_id"),
            "report": self.client.last_model_output(final),
        }
        if upload_info:
            # What went up, so the user is not guessing about a 900 MB upload.
            result.update({
                "environment_id": upload_info["id"],
                "archive_bytes": upload_info["archive_bytes"],
                "archive_files": upload_info["archive_files"],
            })
        if status != "completed":
            return result  # incomplete / failed: nothing to bring back

        return self._deliver(
            task_id=task_id, branch=branch,
            environment_id=final.get("environment_id"),
            repo_url=repo_url, repo_name=repo_name,
            result=result, execute_tests=execute_tests,
            delete_env=delete_env, base_commit=base_commit,
        )

    def _rebuild_workspace_tar(self, environment_id: str, repo_name: str,
                               dest: str) -> None:
        """P1-2: reconstruct the workspace tar file by file.

        The whole-tree download has measured ceilings (2 GiB rejected;
        large TARs dying around 122 s), but the sandbox still holds every
        file and the per-file transport is the one ``fetch --path``
        already uses, so a too-big workspace is a transport problem, not a
        lost task. The rebuilt tar carries the same layout
        :func:`apply_to_branch` expects (entries under ``<repo_name>/``),
        so the delivery reads it exactly as it reads the service's tar.

        ``.git`` metadata and build artifacts (EXCLUDED_DIRS) are skipped:
        the delivery purges them anyway (_purge_artifacts), so downloading
        them would spend quota only to throw the bytes away.

        Raises on the first file that cannot be read; the partial tar is
        left for the caller's cleanup and the delivery never starts on it.
        """
        entries = self.files.list_files(environment_id, "workspace")
        files = [e for e in entries
                 if e.get("type") != "DIRECTORY" and e.get("path")]
        if not files:
            raise ValueError(
                "the per-file fallback got an empty workspace listing; "
                "the sandbox likely expired")
        with tarfile.open(dest, "w") as tar:
            for entry in files:
                rel = entry["path"].strip("/")
                if not rel.startswith("workspace/"):
                    continue  # outside the mounted workspace: not the delivery
                member = rel[len("workspace/"):]
                if not member or any(part in EXCLUDED_DIRS
                                     for part in member.split("/")):
                    continue
                fd, tmp_name = tempfile.mkstemp(prefix="worker-fb-",
                                                dir=self.workdir)
                os.close(fd)
                try:
                    written = self.files.download_to(
                        environment_id, entry["path"], tmp_name,
                        recursive=False)
                    info = tarfile.TarInfo(member)
                    info.size = written
                    info.mode = 0o644
                    with open(tmp_name, "rb") as fh:
                        tar.addfile(info, fh)
                finally:
                    os.remove(tmp_name)

    def _undelivered(self, task_id, *, branch, environment_id, result, exc):
        """Record a task whose agent finished but whose bytes did not arrive.

        `undelivered` exists because the status is load-bearing twice, and both
        times the wrong word costs the user their work:

        - `clean --yes` deletes the sandbox of any task in FINISHED_STATUSES.
          For this task the sandbox is the ONLY copy of the work, so it must
          not count as finished (see FINISHED_STATUSES).
        - `completed` would tell the user to review a branch that was never
          created; `failed` would suggest the task itself needs re-running,
          and its turn is over and paid for.

        The service's own words are kept: 500 and 504 mean different things to
        the user (one names the size of what the agent produced), and this tool
        has no theory about the deadline the service hits.
        """
        detail = str(exc).strip() or type(exc).__name__
        self.store.record({"task_id": task_id, "status": "undelivered",
                           "branch": branch, "environment_id": environment_id,
                           "delivery_error": detail})
        result.update({"status": "undelivered", "delivery_error": detail,
                       "environment_id": environment_id})
        return result

    def _inline_sources(self, *, agents_file: str | None,
                        mount_files=None) -> list:
        """Local files to place inside the sandbox, with the total ceiling kept.

        ``agents_file`` is the shortcut for the file the service auto-loads;
        ``mount_files`` are explicit ``local:target`` pairs already split by
        the caller, for skills and anything else the task needs in place.
        """
        sources, used = [], 0
        if agents_file:
            source = inline_source(agents_file, AGENTS_TARGET)
            used += len(base64.b64decode(source["content"]))
            sources.append(source)
        for local_path, target in mount_files or []:
            source = inline_source(local_path, target, total_bytes=used)
            used += len(base64.b64decode(source["content"]))
            sources.append(source)
        return sources

    def _round_persister(self, task_id: str):
        """Persist the live interaction after every loop round (hardening S7).

        Each round creates a NEW interaction, and a process killed between
        rounds would otherwise leave the record naming the previous one with
        a creation-time status -- so `continue` refused and `status --live`
        read a stale id. Writing the newest id with status `in_progress`
        after every round makes the record resumable at any point.
        """
        def persist(interaction: dict) -> None:
            self.store.record({
                "task_id": task_id,
                "interaction_id": interaction.get("id"),
                "status": "in_progress",
            })
        return persist

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

    def _reconcile_terminal(self, task_id: str, record: dict, current: dict,
                            *, execute_tests: bool) -> dict:
        """Collect a terminal interaction the local record never caught up.

        Spec D-2: the process died after the agent finished but before this
        record was brought up to date. ``current`` is the interaction
        the record names and Google already reports TERMINAL. It is a paid
        result never collected, not a conversation to pay again: the record
        is updated in place and the original interaction id is kept.
        """
        status = current.get("status")
        tokens = self.client.usage(current).get("total_tokens")
        report = self.client.last_model_output(current)
        self.store.record({"task_id": task_id, "status": status,
                           "tokens": tokens, "report": report})
        result = {
            "task_id": task_id, "status": status, "tokens": tokens,
            "budget": record.get("budget"), "branch": record.get("branch"),
            "environment_id": current.get("environment_id")
            or record.get("environment_id"),
            "report": report,
        }
        if status != "completed":
            return result  # failed/canceled/expired: nothing to bring back
        # The record already names the repository and branch the first turn
        # declared; a completed reconcile delivers exactly like a run does.
        repo_url = record.get("repo_url")
        repo_dir = record.get("repo_dir")
        repo_name = os.path.basename((repo_dir or "").rstrip("/")) or None
        if not repo_url or not repo_name:
            return result  # ask-style record: the text report is the result
        branch = record.get("branch") or f"worker/{repo_name}-{task_id}"
        return self._deliver(
            task_id=task_id, branch=branch,
            environment_id=current.get("environment_id")
            or record.get("environment_id"),
            repo_url=repo_url, repo_name=repo_name, result=result,
            execute_tests=execute_tests, delete_env=False,
            base_commit=record.get("base_commit"),
        )

    def continue_task(self, *, task_id: str,
                      budget: int | None = None,
                      memory_save: bool = False,
                      execute_tests: bool = False) -> dict:
        record = self.store.get(task_id)
        # Hardening S7: a mid-loop death leaves the record `in_progress` with
        # the newest interaction id. That is a resumable conversation, not a
        # finished one, so it continues exactly like a budget stop.
        resumable = {"incomplete", "in_progress"}
        if record.get("status") not in resumable:
            raise ValueError(
                f"task {task_id} is {record.get('status')!r}; "
                "only an incomplete or interrupted (in_progress) task can "
                "continue"
            )
        config = agent_config(model=record.get("model"), budget=budget)
        # P0-2: task-scoped dispatcher for this continuation's permission.
        dispatcher = self._task_dispatcher(memory_save)
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
            # Spec D-2: an interaction the service already reports TERMINAL
            # (completed/failed/canceled/expired) is a paid result never
            # collected, not a conversation to restart. Reconcile it in place.
            # `incomplete` (a budget stop) is not in FINISHED_STATUSES, so it
            # falls through to the chain-ahead flow below.
            if current.get("status") in self.FINISHED_STATUSES:
                return self._reconcile_terminal(
                    task_id, record, current,
                    execute_tests=execute_tests)
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
        if dispatcher is not None:
            tools.extend(dispatcher.tool_specs())
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
            final = run_until_complete(self.client, dispatcher,
                                       interaction,
                                       system_instruction=contract,
                                       tools=tools,
                                       on_round=self._round_persister(
                                           task_id))
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
        result = {
            "task_id": task_id,
            "status": status,
            "tokens": tokens,
            "budget": config["max_total_tokens"],
            "branch": record.get("branch"),
            "environment_id": final.get("environment_id"),
            "report": self.client.last_model_output(final),
        }
        if status != "completed":
            return result  # incomplete / failed: nothing to bring back
        # Hardening S3: a continuation that completed is a paid-for result,
        # and it comes home through the same delivery a run uses. The record
        # already names the repository and branch the first turn declared.
        repo_url = record.get("repo_url")
        repo_dir = record.get("repo_dir")
        repo_name = os.path.basename((repo_dir or "").rstrip("/")) or None
        if not repo_url or not repo_name:
            # An ask-style record has no repository to deliver to; the text
            # report is the whole result.
            return result
        # A record from before the branch was pinned names none; derive the
        # same shape `run` uses so the work still lands somewhere reviewable.
        branch = record.get("branch") or f"worker/{repo_name}-{task_id}"
        return self._deliver(
            task_id=task_id, branch=branch,
            environment_id=final.get("environment_id"),
            repo_url=repo_url, repo_name=repo_name,
            result=result, execute_tests=execute_tests,
            delete_env=False,
            base_commit=record.get("base_commit"),
        )

    # Statuses after which an environment is certainly no longer needed.
    # `undelivered` is deliberately NOT here: that task's sandbox is the only
    # copy of work the agent already paid for, so `clean` keeps it (F1).
    FINISHED_STATUSES = ("completed", "canceled", "expired", "failed")

    def probe(self) -> dict:
        """Answer whether this account can upload right now (S12).

        The fair-use window is not readable from anywhere: the only honest
        test of it is an upload, and the honest size for a real task is far too
        expensive to spend on a question. One MiB into a scratch sandbox costs
        about a second at the rate the service imposes and returns the same
        answer, so a user (or his agent) can choose a route on a fact from a
        minute ago instead of on an average.

        No interaction is created: this measures bytes, and spending
        interaction quota to ask about bytes would be the wrong bill.
        """
        environment_id = None
        try:
            environment = self.environments.create_environment()
            environment_id = environment.get("id")
            if not environment_id:
                raise ValueError("the service did not return an environment id")
            started = time.monotonic()
            self.uploads.upload(
                environment_id=environment_id,
                path=PROBE_TARGET,
                data=b"\0" * PROBE_BYTES,
            )
            seconds = time.monotonic() - started
            outcome = {"ok": True, "bytes": PROBE_BYTES, "seconds": seconds,
                       "rate": PROBE_BYTES / seconds if seconds else 0.0}
        except UploadError as exc:
            outcome = {"ok": False, "blocked": is_size_limit(exc.status, exc.body),
                       "status": exc.status, "body": exc.body}
        finally:
            # Every exit path lands here, including the refused upload and a
            # broken client: a probe that left sandboxes behind would itself
            # become the thing that fills the account.
            if environment_id:
                outcome["environment_id"] = environment_id
                outcome["released"] = self._release_environment(environment_id)
        return outcome

    def clean(self, *, apply: bool = False) -> dict:
        """List (and optionally delete) sandboxes this account still holds.

        Nothing else ever removes an environment: a finished task leaves its
        sandbox -- and the repository mounted inside it -- alive for the rest
        of the 7-day TTL. Deletion is irreversible and the service has no
        per-project scope (these are the project's environments on the API
        side, not this tool's), so the default is a dry run and work that is
        not finished is never touched -- in ANY of the local project stores,
        because the environment list is account-wide (F2).
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
            for store in list_project_stores(self.store.root)
            for task in store.list()
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

    # --- reading the sandbox back (S13) ---------------------------------
    #
    # `run` always pulled the whole workspace as one tar, which is the right
    # default for a task but a poor way to answer "what did it produce?" or
    # "give me that one file": the whole tree moves for one file's answer.
    # The client already had both granularities and nothing reached them; the
    # two methods below are that reach.

    def _fetch_target(self, task_id: str) -> tuple[dict, str]:
        """The record and the sandbox to read, or why there is none.

        Everything is resolved before a request goes out: an unknown task or a
        task that never had a sandbox costs the user nothing and says which.
        """
        record = self.store.get(task_id)
        environment_id = record.get("environment_id")
        if not environment_id:
            raise ValueError(
                f"task {task_id} has no sandbox recorded, so there is nothing "
                "to read: only a `run` mounts files (an `ask` task has no "
                "workspace)"
            )
        return record, environment_id

    def _fetch_failure(self, exc: InteractionsError, environment_id: str) -> dict:
        """A refused read, with the cause named only when it is actually known.

        A 404 from this endpoint means one of two opposite things, and the
        bodies are identical. Measured live 2026-11-07, against a sandbox that
        expired and a sandbox holding 3.6 GiB:

            Path 'workspace' not found in environment '...'.      (expired)
            Path 'workspace/no-such-dir' not found in environment '...'.  (alive)

        So the answer is not in the error. It is in whether the sandbox still
        holds anything: a live one lists its root, an expired one answers an
        empty listing. That is one metadata call, on a path the user already
        failed, so it costs nothing worth naming.

        Guessing expiry was not harmless: it told the user to start a new run,
        which spends quota and a branch on a task whose sandbox was fine, and
        it hid the real fix (the path they typed).
        """
        result = {"ok": False, "status": exc.status, "body": exc.body,
                  "expired": False, "missing_path": False}
        if exc.status != 404:
            return result
        try:
            held = self.files.list_files(environment_id, "")
        except InteractionsError:
            # Cannot tell the two apart: say so rather than pick one.
            return result
        if held:
            result["missing_path"] = True
            # The useful hint is what exists one level down, not the whole
            # recursive tree: the user is looking for the path they mistyped.
            top = {"/".join((e.get("path") or "").split("/")[:2])
                   for e in held if e.get("path")}
            result["present"] = sorted(p for p in top if p)[:20]
        else:
            result["expired"] = True
        return result

    def list_workspace(self, task_id: str, path: str = "workspace") -> dict:
        """List what the sandbox holds, without downloading a single file."""
        _, environment_id = self._fetch_target(task_id)
        try:
            entries = self.files.list_files(environment_id, path)
        except InteractionsError as exc:
            result = self._fetch_failure(exc, environment_id)
        else:
            result = {"ok": True, "files": entries}
        result.update({"task_id": task_id,
                       "environment_id": environment_id, "path": path})
        return result

    def fetch_file(self, task_id: str, path: str, dest: str,
                   *, overwrite: bool = False) -> dict:
        """Bring back one file. ``dest`` is never overwritten unasked.

        A file named here usually has a local cousin, and quietly replacing it
        is the one way this read could damage the user's tree; refusing and
        naming --out/--overwrite is cheaper than a lost file.
        """
        _, environment_id = self._fetch_target(task_id)
        if os.path.isdir(dest):
            # A directory is a drop target, not a file to replace.
            dest = os.path.join(dest, os.path.basename(path.strip("/")))
        if os.path.exists(dest) and not overwrite:
            raise ValueError(
                f"{dest} already exists and was left untouched: fetch a file "
                "somewhere else with --out PATH, or say you mean it with "
                "--overwrite"
            )
        try:
            # recursive=False: this is one file, not a tree to tar.
            written = self.files.download_to(environment_id, path, dest,
                                             recursive=False)
        except InteractionsError as exc:
            result = self._fetch_failure(exc, environment_id)
        else:
            result = {"ok": True, "bytes": written, "dest": dest}
        result.update({"task_id": task_id, "environment_id": environment_id,
                       "path": path})
        return result

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
            stream: bool = False, background: bool = False,
            images=None, memory_save: bool = False) -> dict:
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
        # P0-2: task-scoped dispatcher for this ask's permission.
        dispatcher = self._task_dispatcher(memory_save)
        task_id = new_task_id()

        tools = [{"type": "code_execution"}, {"type": "google_search"},
                 {"type": "url_context"}]
        if dispatcher is not None:
            tools.extend(dispatcher.tool_specs())
        contract = worker_contract(
            project=proj, repo_dir="(no repository mounted)",
            memory_tools=self.memory_enabled,
        )

        if stream:
            # Hardening S8: send the same input the non-stream branch sends
            # (text plus any images) so --stream --image does not drop them.
            interaction = self.client.stream_interaction(
                agent=AGENT,
                input=task_parts(task, images),
                environment="remote",
                agent_config=config,
                system_instruction=contract,
                tools=tools,
                on_delta=lambda t: print(t, end="", flush=True),
            )
        else:
            interaction = self.client.create_interaction(
                agent=AGENT,
                input=task_parts(task, images),
                environment="remote",
                agent_config=config,
                system_instruction=contract,
                tools=tools,
                background=background,
            )
        self.store.record(create=True, task={
            "task_id": task_id, "kind": "ask",
            "status": interaction.get("status"),
            "interaction_id": interaction.get("id"),
            "environment_id": interaction.get("environment_id"),
            "model": config["model"], "budget": config["max_total_tokens"],
            "task": task,
        })

        final = run_until_complete(self.client, dispatcher, interaction,
                                   system_instruction=contract, tools=tools,
                                   on_round=self._round_persister(task_id),
                                   call_cache=_LocalCallCache(self.store,
                                                              task_id))
        if background:
            try:
                final = wait_until_done(
                    self.client, dispatcher, final,
                    poll_interval=self.poll_interval,
                    max_polls=self.max_polls,
                    system_instruction=contract, tools=tools,
                    on_round=self._round_persister(task_id),
                    call_cache=_LocalCallCache(self.store, task_id),
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
            "streamed": stream,
        }
