"""Local task state: which interaction and environment belong to which task.

The remote service keeps its own conversation; this module keeps the mapping
the user needs to resume, inspect or cancel a task after the terminal closed.
The file is private (0600) because it names the user's environments, and it
is plain JSON so it can be read with any tool.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid

DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".remote-worker")


def new_task_id() -> str:
    """A task id that is unique even for two launches in the same second.

    The second stays in the name because it is what a human sorts and
    recognizes; the random suffix is what makes it unique (hardening S5).
    """
    return f"t_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _slug(project: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", project).strip("-")
    return clean or "default"


def list_project_stores(root: str | None = None) -> list:
    """One TaskStore per project directory that holds a state.json.

    The service lists environments per account, not per project, so the
    protection set for ``clean`` must be built from every local store,
    not only the one the CLI was run from: an unfinished task in another
    project still owns a live sandbox, and deleting it would lose work.
    """
    base = root or DEFAULT_ROOT
    try:
        names = sorted(os.listdir(base))
    except FileNotFoundError:
        return []
    return [
        TaskStore(project=name, root=base)
        for name in names
        if os.path.isfile(os.path.join(base, name, "state.json"))
    ]


class TaskStore:
    """Per-project task records, newest first."""

    def __init__(self, project: str, root: str | None = None):
        if not project:
            raise ValueError("project is required")
        self.project = project
        base = root or DEFAULT_ROOT
        self.root = base
        self._dir = os.path.join(base, _slug(project))
        self.path = os.path.join(self._dir, "state.json")

    # --- reading ---------------------------------------------------------

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {"project": self.project, "tasks": {}}
        data.setdefault("tasks", {})
        return data

    def _save(self, data: dict) -> None:
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        finally:
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    # --- writing ---------------------------------------------------------

    def _lock(self):
        """An exclusive cross-process lock around one read-modify-write.

        ``record`` loads the whole ``state.json``, mutates one task, and
        writes it back. Two processes doing that at the same time both read
        the same snapshot and the last writer silently drops the other's
        fields (P0-2). The lock serialises the whole critical section so no
        update is lost -- and it also stops two processes racing on the
        same ``.tmp`` rename path, which failed outright.
        """
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        fd = os.open(self.path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        return fd

    def record(self, task: dict, *, create: bool = False) -> dict:
        """Insert or merge one task record, keyed by ``task_id``.

        Merging is how a task's own status updates accumulate, including a
        continuation that legitimately moves its interaction id forward. It
        must never fuse two DIFFERENT tasks, so a caller that is CREATING a
        task passes ``create=True``: an id already owned by another
        interaction then refuses instead of overwriting a paid-for record
        (hardening S5). The whole read-modify-write runs under a
        cross-process lock (P0-2) so concurrent tasks never lose an update.
        """
        task_id = task.get("task_id")
        if not task_id:
            raise ValueError("task_id is required")
        fd = self._lock()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            data = self._load()
            existing = data["tasks"].get(task_id, {})
            incoming = task.get("interaction_id")
            owned = existing.get("interaction_id")
            if create and owned and incoming and incoming != owned:
                # Two different tasks share an id; report the collision
                # instead of destroying a paid-for record.
                raise ValueError(
                    f"task_id {task_id!r} already names another task "
                    f"(interaction {owned!r}, not {incoming!r})"
                )
            existing.update(task)
            data["tasks"][task_id] = existing
            self._save(data)
            return existing
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # --- queries ---------------------------------------------------------

    def get(self, task_id: str) -> dict:
        data = self._load()
        try:
            return data["tasks"][task_id]
        except KeyError:
            raise KeyError(f"unknown task {task_id!r}") from None

    def list(self) -> list[dict]:
        tasks = list(self._load()["tasks"].values())
        tasks.reverse()  # newest first: insertion order, reversed
        return tasks