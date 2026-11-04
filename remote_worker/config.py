"""User configuration: the model and budget a run uses by default.

Written by ``remote-worker setup``. The file sits next to the task state, is
private (0600) and is replaced with a timestamped backup so a change of mind
is always reversible.
"""

from __future__ import annotations

import json
import os
import shutil
import time

from . import planner
from .state import DEFAULT_ROOT


def config_path(root: str | None = None) -> str:
    return os.path.join(root or DEFAULT_ROOT, "config.json")


def load_config(root: str | None = None) -> dict:
    """Return the stored settings, falling back to the planner defaults."""
    defaults = {
        "model": planner.DEFAULT_MODEL,
        "budget": planner.DEFAULT_BUDGET,
    }
    path = config_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return defaults
    keys = ("model", "budget", "api_key")
    merged = {**defaults, **{
        key: stored[key] for key in keys if key in stored
    }}
    return merged


def save_config(*, model: str | None, budget: int | None,
                api_key: str | None = None,
                root: str | None = None) -> str:
    """Validate, back up any previous file, and write the new settings.

    Returns the path written. Raises :class:`UnsupportedModelError` or
    ``ValueError`` before touching the disk when the values are invalid.
    """
    chosen_model = model or planner.DEFAULT_MODEL
    chosen_budget = planner.DEFAULT_BUDGET if budget is None else int(budget)
    # Validate through the planner so the CLI and the file cannot disagree.
    planner.agent_config(model=chosen_model, budget=chosen_budget)

    directory = root or DEFAULT_ROOT
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = config_path(directory)

    # An omitted api_key keeps the previously stored one, so re-running
    # setup never silently loses the user's key.
    stored_key = load_config(root).get("api_key") if os.path.exists(path) else None
    key = api_key if api_key else stored_key

    if os.path.exists(path):
        stamp = time.strftime("%Y%m%dT%H%M%S")
        shutil.copy2(path, f"{path}.bak-{stamp}")

    payload = {"model": chosen_model, "budget": chosen_budget}
    if key:
        payload["api_key"] = key

    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    finally:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def resolve_api_key(root: str | None = None) -> str | None:
    """The environment variable wins; the stored config is the fallback.

    Returns None when neither is set, so offline commands (status, setup)
    keep working.
    """
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env
    return load_config(root).get("api_key")
