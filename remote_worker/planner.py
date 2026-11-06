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

import base64

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


#: Our own ceiling for one input image. The service does not publish a per-
#: image limit, so this is a guard against reading a whole directory of
#: screenshots into one request body -- it is ours, and says so.
IMAGE_INPUT_LIMIT_BYTES = 20 * 1024 * 1024

#: Magic numbers for the image kinds Antigravity accepts. The type is taken
#: from the bytes, never from the extension: a file named .jpg that holds a
#: PNG must be sent as what it is, or the service cannot decode it.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _image_mime(head: bytes) -> str | None:
    for signature, mime in _IMAGE_SIGNATURES:
        if head.startswith(signature):
            return mime
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def task_parts(task: str, images=None):
    """Build the ``input`` for one turn: the text, then any images.

    Antigravity reads text and image files, and an image can only reach it as
    a part of the request — "look at this screenshot" is otherwise a task
    about a file the agent has never seen. Returns the plain string when there
    are no images, so the common request keeps the shape it always had.

    Audio, video and documents are refused rather than tried: the agent does
    not read them, so sending one buys a confusing failure.
    """
    if not images:
        return task
    parts = [{"type": "text", "text": task}]
    for path in images:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise ValueError(
                f"cannot read the image {path}: {exc.strerror}"
            ) from exc
        mime = _image_mime(raw[:16])
        if mime is None:
            raise ValueError(
                f"{path} is not an image this agent can read; it accepts "
                "png, jpeg, gif and webp (audio, video and documents are not "
                "supported)"
            )
        if len(raw) > IMAGE_INPUT_LIMIT_BYTES:
            raise ValueError(
                f"{path} is {len(raw)} bytes; this tool caps one input image "
                "at 20 MB of its own guard, not the service's"
            )
        parts.append({
            "type": "image",
            "mime_type": mime,
            "data": base64.b64encode(raw).decode("ascii"),
        })
    return parts


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