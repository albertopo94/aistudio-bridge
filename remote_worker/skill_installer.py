"""Install, back up and restore the remote-worker skill in a local agent.

gentle-shell (and other agents) load skills from
``<root>/skills/<name>/SKILL.md``. This module writes the skill that teaches
the LLM to invoke the ``remote-worker`` CLI, and mirrors the
agy-model-bridge backup pattern: every install keeps a timestamped backup,
and restore rolls back to a specific backup or the most recent one (or
removes the skill if its original state was "not installed").
"""

from __future__ import annotations

import datetime
import os
import shutil
from pathlib import Path

from .routes import format_routes

SKILL_NAME = "remote-worker"
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".agents")


def _skill_dir(root: str) -> Path:
    return Path(root) / "skills" / SKILL_NAME


def _skill_path(root: str) -> Path:
    return _skill_dir(root) / "SKILL.md"


def _backup_of(path: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _zero_state_backup(path: Path) -> Path:
    """Marker backup for a target that did not previously exist."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    backup = path.with_name(f"{path.name}.backup-{stamp}-original")
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text('{"_zero_state": true}\n', encoding="utf-8")
    return backup


def skill_content() -> str:
    """The SKILL.md body. Kept as a function so it can be regenerated."""
    return """---
name: remote-worker
description: >-
  Trigger: delegating a task to Google's Antigravity agent (delegate, use the
  remote worker, ask Google to build/research/analyze), or when the user wants
  to offload token cost from the local model to Google's free tier, or when a
  task is heavy, isolated and reviewable. Invokes the local `remote-worker` CLI
  as an external tool; never apply or merge the result yourself.
---

# remote-worker

You can delegate work to Google's Antigravity managed agent through the local
`remote-worker` CLI. It runs inside a remote sandbox on Google's side; the
result comes back to the user's machine. Your job is to invoke the CLI and
report, never to fabricate or apply the diff yourself.

## When to delegate

Delegate to the remote worker when ANY of these hold:

- the user explicitly asks to use it (always wins);
- the user is low on local model tokens and wants to spend Google's free
  tier instead;
- the task is heavy and can run isolated in a sandbox (builds, refactors,
  generating/analyzing a lot of code, standalone scripts);
- the task is research or analysis that returns a structured result;
- the result is reviewable/reversible (code comes back as a diff on a branch).

Work locally in this shell when:

- the task depends on the local filesystem, secrets or config of this machine;
- the task requires iterating with the user and exploring this environment;
- the task is trivial and fast (a one-line diff does not justify a sandbox);
- the task is memory/discussion that must stay in this session.

## How to delegate

Two modes:

- `remote-worker run "<task>" --repo <repo-url>` for CODE tasks: the worker
  edits a clone of the repo and returns a diff on a branch. The user reviews
  and merges; you never apply the diff.
- `remote-worker ask "<task>"` for NON-code tasks (research, analysis,
  generation): returns the worker's text directly.

The API key is resolved in order: the `GEMINI_API_KEY` environment
variable wins, then the stored config in `~/.remote-worker/config.json`
(written by `remote-worker setup`, 0600). Never print it.

## Getting files into the sandbox

When the task needs a repository, one of these routes carries it. The table
below is the same one `remote-worker routes` prints, so the numbers here and
the numbers the CLI reports cannot disagree.

```
""" + format_routes() + """
```

How to use it: `--repo` is the default and costs the user nothing in upload
time. Reach for `--local-repo` only when the repository is not on a host the
sandbox can see, and warn the user first that the service uploads at ~1.5
MiB/s and that the account has a fair-use window over time: a refusal with
"File size exceeds maximum allowed limit" means the window is spent, not that
the repository is too big, and retrying later is the fix. Offer the other
routes from the table instead of retrying the same one.

## Rules

- Always show the user the report (status, tokens, branch/diff, test result).
- Never fabricate a diff, a test result, or the worker's output.
- Never run git push/merge on the worker's result; that is the user's call.
- A status of `incomplete` is not a failure: tell the user the exact
  `remote-worker continue <task_id>` command.
"""
# --- public API (the CLI calls these) ---

def install_skill(*, root: str = DEFAULT_ROOT, dry_run: bool = False) -> str:
    """Write the remote-worker skill under <root>/skills/, backing up any
    previous file first. Returns the path written."""
    path = _skill_path(root)
    if path.exists() and path.is_file():
        _backup_of(path)
    elif not dry_run:
        _zero_state_backup(path)
    if dry_run:
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(skill_content(), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def list_backups(*, root: str = DEFAULT_ROOT) -> list[str]:
    """Absolute paths of the backups for the skill, newest first."""
    path = _skill_path(root)
    parent = path.parent
    if not parent.exists():
        return []
    backups = sorted(
        p for p in parent.glob(path.name + ".backup-*")
        if not p.name.endswith("-original")
    )
    backups.reverse()
    return [str(p) for p in backups]


def restore_skill(*, root: str = DEFAULT_ROOT,
                  backup: str | None = None) -> str:
    """Restore the skill from a specific backup or the most recent one.

    Returns the path restored. Restoring an ``-original`` (zero-state) backup
    removes the skill entirely, mirroring agy-model-bridge's factory reset.
    """
    path = _skill_path(root)
    if backup is None:
        backups = list_backups(root=root)
        if not backups:
            # Fall back to the zero-state marker so a single install can be
            # undone (factory reset), mirroring agy-model-bridge.
            originals = sorted(
                p for p in path.parent.glob(path.name + ".backup-*-original")
            )
            if not originals:
                raise FileNotFoundError(f"No backups found for {path}")
            backups = [str(originals[-1])]
        chosen = Path(backups[0])
    else:
        chosen = Path(backup)
        if not chosen.exists():
            raise FileNotFoundError(f"Backup file not found: {chosen}")

    if chosen.name.endswith("-original"):
        path.unlink(missing_ok=True)
        return str(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(chosen, path)
    return str(path)
