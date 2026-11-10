"""Local memory backend for the remote worker (Engram CLI).

The remote agent never touches the Engram store directly. It *requests* a
memory operation through a custom function; this module answers that
request on the user's machine.

Two rules are enforced here and nowhere else:

* **The project is owned by the orchestrator.** ``EngramMemory`` pins it on
  every CLI call, and the dispatcher discards any ``project`` argument the
  worker invents, so a remote turn can never read or write another
  project's memory.
* **Results are JSON strings.** A JSON array in ``function_result.result``
  is rejected by the Interactions API (HTTP 400, PoC finding); the
  dispatcher always returns a serialized object.
"""

from __future__ import annotations

import json
import re
import subprocess

DEFAULT_BINARY = "engram"

#: The only memory operations a remote worker may ever request. Admin
#: operations (delete, prune, merge) are deliberately absent.
ALLOWED_TOOLS = frozenset({"memory_search", "memory_save", "memory_context"})

#: The half of that allowlist a worker gets without the user saying anything.
#: Hardening S6: a prompted agent decides what `memory_save` would write, and
#: that text outlives the task in the user's durable store (and steers later
#: sessions), so writing is opt-in through `--memory-save`.
READ_ONLY_TOOLS = frozenset({"memory_search", "memory_context"})
WRITE_TOOLS = frozenset({"memory_save"})

#: Bounds for an accepted write (hardening S10). Generous for a real
#: decision, small enough that a poisoned turn cannot fill the store.
SAVE_TITLE_LIMIT = 200
SAVE_CONTENT_LIMIT = 8000
#: The category travels as a CLI flag, so it stays a plain word.
SAVE_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class EngramError(RuntimeError):
    """The local Engram CLI failed; the message carries its stderr."""


class UnknownToolError(ValueError):
    """The worker requested a memory operation outside the allowlist."""


class EngramMemory:
    """Runs the local ``engram`` CLI with the project pinned."""

    def __init__(self, project: str, binary: str = DEFAULT_BINARY,
                 runner=None, timeout: float = 60.0):
        if not project:
            raise ValueError("project is required")
        self.project = project
        self._binary = binary
        self._timeout = timeout
        self._runner = runner or self._run_cli

    def _run_cli(self, argv: list[str]) -> str:
        try:
            proc = subprocess.run(
                [self._binary, *argv],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise EngramError(
                f"engram binary not found: {self._binary}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise EngramError(
                f"engram timed out after {self._timeout}s: {' '.join(argv)}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() or (
                f"engram exited with status {proc.returncode}"
            )
            raise EngramError(detail)
        return (proc.stdout or "").strip()

    def search(self, query: str, limit: int = 5) -> str:
        return self._runner([
            "search", query,
            "--limit", str(limit),
            "--project", self.project,
        ])

    def save(self, title: str, content: str, type: str = "discovery") -> str:
        return self._runner([
            "save", title, content,
            "--type", type,
            "--project", self.project,
        ])

    def context(self, scope: str | None = None) -> str:
        argv = ["context", "--project", self.project]
        if scope:
            argv += ["--scope", scope]
        return self._runner(argv)


class MemoryDispatcher:
    """Translates a worker's ``function_call`` into a local memory answer.

    Reads are always available; writes need ``allow_save`` (the CLI's
    ``--memory-save``), and the tool is not even advertised without it, so a
    worker cannot be talked into a write the user never asked for.

    ``allow_save`` is set once at construction and read-only afterwards
    (P0-2): a task's memory permission is immutable and specific to that
    task, so a concurrent task sharing infrastructure can never flip it.
    """

    def __init__(self, memory: EngramMemory, allow_save: bool = False):
        self._memory = memory
        # Immutable per task (P0-2): a task-scoped permission is set once at
        # construction and cannot be flipped by another task sharing a
        # dispatcher. The runner builds a fresh dispatcher per task with the
        # task's own flag instead of mutating a shared one.
        self._allow_save = allow_save

    @property
    def allow_save(self) -> bool:
        """Whether this dispatcher may persist memory writes. Read-only."""
        return self._allow_save

    def tool_specs(self) -> list[dict]:
        """Declarations to pass as the ``tools`` entry of an interaction."""
        specs = [
            {
                "type": "function",
                "name": "memory_search",
                "description": (
                    "Search the user's persistent project memory for past "
                    "decisions, discoveries and durable constraints. Returns "
                    "matching memories."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language search query",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 5)",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "type": "function",
                "name": "memory_context",
                "description": (
                    "Load recent context from previous sessions for the "
                    "current project."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        ]
        if self.allow_save:
            specs.append({
                "type": "function",
                "name": "memory_save",
                "description": (
                    "Save a durable decision, discovery or constraint to the "
                    "user's project memory. Do not save conversation turns. "
                    f"Title up to {SAVE_TITLE_LIMIT} characters, content up "
                    f"to {SAVE_CONTENT_LIMIT}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "type": {
                            "type": "string",
                            "description": (
                                "Category, e.g. decision, discovery, bugfix"
                            ),
                        },
                    },
                    "required": ["title", "content"],
                },
            })
        return specs

    def _check_write(self, arguments: dict) -> None:
        """Gate an opt-in write: the policy plus S10's validation (S10)."""
        title = arguments.get("title") or ""
        content = arguments.get("content") or ""
        if not title.strip():
            raise ValueError("memory_save: title is required")
        if not content.strip():
            raise ValueError("memory_save: content is required")
        if len(title) > SAVE_TITLE_LIMIT:
            raise ValueError(
                f"memory_save: title is {len(title)} characters; the limit "
                f"is {SAVE_TITLE_LIMIT}"
            )
        if len(content) > SAVE_CONTENT_LIMIT:
            raise ValueError(
                f"memory_save: content is {len(content)} characters; the "
                f"limit is {SAVE_CONTENT_LIMIT}"
            )
        write_type = arguments.get("type") or "discovery"
        if not SAVE_TYPE_PATTERN.match(str(write_type)):
            raise ValueError(
                "memory_save: type must be a short word of letters, digits, "
                "hyphens or underscores (e.g. decision, discovery, bugfix)"
            )

    def dispatch(self, call: dict) -> str:
        """Execute one ``function_call`` and return a JSON **string**."""
        name = call.get("name")
        if name not in ALLOWED_TOOLS:
            raise UnknownToolError(
                f"memory operation not allowed: {name!r} "
                f"(allowed: {sorted(ALLOWED_TOOLS)})"
            )

        arguments = dict(call.get("arguments") or {})
        # The orchestrator owns the project: ignore anything the worker sent.
        arguments.pop("project", None)

        # Hardening S6: writing needs the user's explicit opt-in, and the
        # answer goes back to the worker as an error it can report, not an
        # exception that would abort the turn.
        if name in WRITE_TOOLS and not self.allow_save:
            return json.dumps({
                "ok": False,
                "tool": name,
                "error": (
                    "the user did not enable memory writes for this task; "
                    "use --memory-save to allow them"
                ),
            })

        try:
            if name == "memory_save":
                self._check_write(arguments)
                content = self._memory.save(
                    arguments.get("title", ""),
                    arguments.get("content", ""),
                    type=arguments.get("type") or "discovery",
                )
            elif name == "memory_search":
                content = self._memory.search(
                    arguments.get("query", ""),
                    limit=int(arguments.get("limit", 5)),
                )
            else:  # memory_context
                content = self._memory.context(arguments.get("scope"))
        except EngramError as exc:
            # A broken or unknown local store must inform the worker, not
            # abort the task: the worker can report "memory unavailable" and
            # keep working with what it has.
            return json.dumps({
                "ok": False,
                "tool": name,
                "error": str(exc),
            })
        except ValueError as exc:
            # A write the policy refuses is reported to the worker the same
            # way a store failure is: it keeps the task moving.
            return json.dumps({
                "ok": False,
                "tool": name,
                "error": str(exc),
            })

        return json.dumps({
            "ok": True,
            "tool": name,
            "content": content,
        })