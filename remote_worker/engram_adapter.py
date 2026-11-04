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
import subprocess

DEFAULT_BINARY = "engram"

#: The only memory operations a remote worker may request. Admin operations
#: (delete, prune, merge) are deliberately absent.
ALLOWED_TOOLS = frozenset({"memory_search", "memory_save", "memory_context"})


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
    """Translates a worker's ``function_call`` into a local memory answer."""

    def __init__(self, memory: EngramMemory):
        self._memory = memory

    def tool_specs(self) -> list[dict]:
        """Declarations to pass as the ``tools`` entry of an interaction."""
        return [
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
                "name": "memory_save",
                "description": (
                    "Save a durable decision, discovery or constraint to the "
                    "user's project memory. Do not save conversation turns."
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

        if name == "memory_search":
            content = self._memory.search(
                arguments.get("query", ""),
                limit=int(arguments.get("limit", 5)),
            )
        elif name == "memory_save":
            content = self._memory.save(
                arguments.get("title", ""),
                arguments.get("content", ""),
                type=arguments.get("type", "discovery"),
            )
        else:  # memory_context
            content = self._memory.context(arguments.get("scope"))

        return json.dumps({
            "ok": True,
            "tool": name,
            "content": content,
        })