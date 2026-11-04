"""Turn a user request into an interaction specification.

Two decisions live here, and only here:

* **Model floor.** Only the two models the user chose are accepted. A lower
  model is rejected instead of silently downgraded: cost and quality are the
  user's call, not a fallback.
* **Worker contract.** The ``system_instruction`` that tells the remote agent
  what it is (a delegated worker), how it may obtain memory (only through
  the tools, never invention) and how it must report.
"""

from __future__ import annotations

#: Refuse anything below this pair.
ALLOWED_MODELS = ("gemini-4-argon", "gemini-3.8-flash")
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_BUDGET = 300_000


class UnsupportedModelError(ValueError):
    """The requested model is below the accepted floor."""


def agent_config(model: str | None = None,
                 budget: int | None = None) -> dict:
    """Build the exact ``agent_config`` for one interaction."""
    chosen = model or DEFAULT_MODEL
    if chosen not in ALLOWED_MODELS:
        raise UnsupportedModelError(
            f"model {chosen!r} is not allowed; choose one of "
            f"{', '.join(ALLOWED_MODELS)}"
        )
    tokens = DEFAULT_BUDGET if budget is None else int(budget)
    if tokens <= 0:
        raise ValueError(f"budget must be positive, got {tokens}")
    return {
        "type": "antigravity",
        "model": chosen,
        "max_total_tokens": tokens,
    }


def worker_contract(*, project: str, repo_dir: str,
                    memory_tools: bool = True) -> str:
    """The worker's ``system_instruction``: role, memory rule, report format."""
    memory_rule = (
        "Memory: for anything about past decisions, project context or "
        "calibration data you MUST call memory_search (and memory_save for "
        "durable findings) and use only what they return. Never invent or "
        "recall memory contents from your own knowledge. If a search returns "
        "nothing, say so explicitly."
        if memory_tools else
        "Memory: no memory tools are available for this task."
    )
    return f"""You are a delegated code worker, not an orchestrator. You execute one
bounded task inside the sandbox and stop.

Project: {project}
Repository: {repo_dir}

Before touching any code, read the project's AGENTS.md in the repository
root and follow its conventions. They take precedence over your defaults.

{memory_rule}

Scope: do exactly the requested change. Do not refactor, rename, reformat or
"improve" adjacent code. Do not delete or rewrite .git.

Delivery: do not push, commit or open pull requests. The orchestrator
retrieves your work from the sandbox; git history belongs to the user.

Report, in this order:
1. what you did (files touched, one line each)
2. evidence (the commands you ran and their output)
3. result (what remains, or "done")"""