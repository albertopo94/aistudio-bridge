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


def network_config(allow_domains=None, no_network=False):
    """Build the ``environment.network`` object, or None for "leave it alone".

    By default the sandbox reaches any host. That is the fence for tasks that
    should not: an allowlist of domains, or no egress at all.

    The service matches domains, not URL patterns, and ``*.example.com`` does
    NOT match ``example.com`` itself, so a wildcard entry without its bare
    companion is reported rather than left to surprise the user later.
    """
    if no_network:
        if allow_domains:
            raise ValueError(
                "--no-network and --allow-domain cannot be combined: the "
                "first closes egress, the second opens a hole in it"
            )
        return {"allowlist": "disabled"}
    if not allow_domains:
        return None
    rules, seen = [], set()
    for raw in allow_domains:
        domain = (raw or "").strip()
        if not domain:
            raise ValueError("an allowed domain cannot be empty")
        if any(ch.isspace() for ch in domain):
            raise ValueError(f"a domain cannot contain spaces: {raw!r}")
        if "/" in domain or domain.startswith("http:") or domain.startswith(
                "https:"):
            raise ValueError(
                f"expected a bare hostname, not a URL: {raw!r} "
                "(the allowlist matches domains, not paths)"
            )
        if domain in seen:
            continue
        seen.add(domain)
        rules.append({"domain": domain})
    if not rules:
        return None
    return {"allowlist": rules}


def wildcard_gaps(allow_domains):
    """Wildcards the user probably meant to pair with their root domain.

    ``*.example.com`` does not match ``example.com``, so an allowlist holding
    only the wildcard silently blocks the host people expect. Reported as
    advice by the CLI, never decided for the user: adding the root may be
    exactly what they meant to exclude.
    """
    listed = {(d or "").strip() for d in (allow_domains or [])}
    return sorted(d[2:] for d in listed
                  if d.startswith("*.") and d[2:] not in listed)


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