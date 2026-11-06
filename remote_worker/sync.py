"""Round trip between the local repository and the remote sandbox.

Two directions, one rule each:

* **In:** the sandbox clones the repository itself from a ``repository``
  source. The URL and the size limit are all this side needs.
* **Out:** the workspace is downloaded as a tar and applied to a *fresh
  local clone on a new branch*. The sandbox's own ``.git`` and build
  artifacts never travel: overlaying them would clobber the clone's history
  and, in the PoC, a root-owned ``__pycache__`` entry aborted ``git add``.

Nothing here pushes. Git truth stays on the user's machine; merging the
branch is the user's decision.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import tarfile
import tempfile

#: Documented cap for a ``repository`` source (docs/agent-environment).
REPOSITORY_SOURCE_LIMIT_BYTES = 500 * 1024 * 1024

#: Measured ceiling of a direct resumable upload into a sandbox (G5 PoC).
#: 153 chunks of 10 MB (1530 MiB) were accepted and the next chunk returned
#: 400 "File size exceeds maximum allowed limit", so the guard sits at the
#: last size the service demonstrably takes rather than at a guessed number.
UPLOAD_LIMIT_BYTES = 1530 * 1024 * 1024

#: Inline sources are capped at 1 MB per file and 2 MB in total by the
#: environment documentation. Enforcing that here turns a request the service
#: would reject into an explanation the user can act on.
INLINE_FILE_LIMIT_BYTES = 1 * 1024 * 1024
INLINE_TOTAL_LIMIT_BYTES = 2 * 1024 * 1024


def tar_gz_bytes(files: dict) -> bytes:
    """Pack ``{path: bytes}`` into a gzip tarball, in memory.

    The upload path (G4/G5) pushes a repository that exceeds the 500 MB
    repository-source cap. Building the archive in memory keeps the ceiling
    explicit and the caller in charge of the total; nothing touches a temp
    file this tool would then have to clean up.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def archive_repo(repo_path: str, dest: str) -> dict:
    """Pack a local git working tree into a gzip tarball at ``dest``.

    The uploaded path exists because the tree is too big for the 500 MB
    ``repository`` source, so the archive streams through :mod:`tarfile`
    instead of being built in memory: a large repository must not have to
    fit in RAM before it can travel.

    What travels is what the user sees in ``git status``: tracked files plus
    untracked-but-not-ignored ones. Ignored build output and ``.git`` stay
    home, which is also what the return path expects, since it overlays files
    onto a fresh clone rather than trusting the sandbox's history.

    Returns ``{"path", "bytes", "files"}``.
    """
    if not os.path.isdir(repo_path):
        raise ValueError(f"local repository {repo_path!r} is not a directory")
    tracked = _git(repo_path, "ls-files", "-z").stdout
    untracked = _git(repo_path, "ls-files", "-z", "--others",
                     "--exclude-standard").stdout
    names = sorted({name for name in tracked.split("\0") if name})
    names += sorted({name for name in untracked.split("\0") if name} - set(names))
    if not names:
        raise ValueError(
            f"local repository {repo_path!r} has no files to archive"
        )
    written = 0
    with tarfile.open(dest, mode="w:gz") as tar:
        for name in names:
            full = os.path.join(repo_path, name)
            if os.path.isdir(full) or not os.path.lexists(full):
                continue
            # recursive=False: git already listed every path, so recursing
            # would both duplicate entries and drag in ignored files.
            tar.add(full, arcname=name, recursive=False)
            written += 1
    return {"path": dest, "bytes": os.path.getsize(dest), "files": written}


def inline_source(local_path: str, target: str,
                  total_bytes: int = 0) -> dict:
    """Build an ``inline`` source: one local file, placed inside the sandbox.

    This is the channel for the two files the service reads by convention and
    a mounted repository cannot reach: ``.agents/AGENTS.md``, which the service
    auto-loads as system instructions, and
    ``.agents/skills/<name>/SKILL.md``, which it auto-discovers as a skill. A
    repository mounts at ``/workspace/<name>``, which is neither place, so a
    project's AGENTS.md is otherwise read only because the worker contract
    orders the agent to open it (G22), and the documented skill channel was
    never used at all (G23).

    Content is base64 and must be text: an inline source is something the
    agent reads, not a blob it executes.
    """
    if not target or not target.startswith("/") or target == "/":
        raise ValueError(
            f"target must be an absolute path inside the sandbox, got "
            f"{target!r}"
        )
    try:
        with open(local_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError(f"cannot read {local_path}: {exc.strerror}") from exc
    if len(raw) > INLINE_FILE_LIMIT_BYTES:
        raise ValueError(
            f"inline sources are capped at 1 MB per file; {local_path} is "
            f"{len(raw)} bytes"
        )
    if total_bytes + len(raw) > INLINE_TOTAL_LIMIT_BYTES:
        raise ValueError(
            "inline sources are capped at 2 MB in total; this file would push "
            f"the total to {total_bytes + len(raw)} bytes"
        )
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"inline sources must be text (utf-8); {local_path} is not"
        ) from exc
    return {
        "type": "inline",
        "content": base64.b64encode(raw).decode("ascii"),
        "encoding": "base64",
        "target": target,
    }

#: Entries inside a downloaded workspace that must never be overlaid.
EXCLUDED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", "node_modules"})


class GitError(RuntimeError):
    """A git command failed; the message carries its stderr."""


def mount_repo(source_url: str, target: str,
               size_bytes: int | None = None) -> dict:
    """Build the ``repository`` source entry for an environment.

    ``size_bytes`` is optional: when the caller already knows the repository
    size, an oversized one is rejected here instead of failing inside the
    sandbox.
    """
    if not source_url:
        raise ValueError("source_url is required")
    if not target or not target.startswith("/"):
        raise ValueError("target must be an absolute path inside the sandbox")
    if size_bytes is not None and size_bytes > REPOSITORY_SOURCE_LIMIT_BYTES:
        raise ValueError(
            f"repository source is capped at "
            f"{REPOSITORY_SOURCE_LIMIT_BYTES // (1024 * 1024)} MB; "
            f"got {size_bytes} bytes"
        )
    return {"type": "repository", "source": source_url, "target": target}


def apply_to_branch(*, tar_path: str, repo_url: str, branch: str,
                    workdir: str, repo_dir: str | None = None) -> dict:
    """Apply a downloaded workspace tar onto a fresh clone, on a new branch.

    Returns ``{"clone_dir", "diff", "changed"}``. ``changed`` lists the
    ``git status --short`` lines so the caller can report what moved without
    re-running git.
    """
    clone = os.path.abspath(workdir)
    if not os.path.isdir(os.path.join(clone, ".git")):
        _git(None, "clone", "-q", repo_url, clone)
    _git(clone, "checkout", "-q", "-B", branch)

    with tempfile.TemporaryDirectory(prefix="worker-tar-") as scratch:
        _extract(tar_path, scratch)
        source_root = _repo_root(scratch, repo_dir)
        _purge_artifacts(source_root)
        _overlay(source_root, clone)

    _git(clone, "add", "-A")
    diff = _git(clone, "diff", "--cached").stdout
    changed = [
        line for line in _git(clone, "status", "--short").stdout.splitlines()
        if line.strip()
    ]
    return {"clone_dir": clone, "diff": diff, "changed": changed}


def run_tests(workdir: str, command: list[str],
              timeout: float = 1800.0) -> tuple[int, str]:
    """Run the project's own test command inside the clone.

    Returns ``(returncode, combined_output)``. A non-zero status is a result,
    not an exception: the caller reports it.
    """
    env = dict(os.environ)
    # The user reviews the clone's git diff after the tests run, so the test
    # run must not add build artifacts that would appear as untracked changes.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            command, cwd=workdir, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, f"test command timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


# --- internals ----------------------------------------------------------

def _git(cwd: str | None, *args: str) -> subprocess.CompletedProcess:
    argv = ["git", *args]
    if cwd is not None:
        argv = ["git", "-C", cwd, *args]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc


def _extract(tar_path: str, dest: str) -> None:
    with tarfile.open(tar_path) as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 has no filter argument
            tar.extractall(dest)


def _repo_root(scratch: str, repo_dir: str | None) -> str:
    if repo_dir:
        root = os.path.join(scratch, repo_dir)
        if not os.path.isdir(root):
            raise ValueError(f"repo_dir {repo_dir!r} not found in the archive")
        return root
    entries = [
        name for name in os.listdir(scratch)
        if os.path.isdir(os.path.join(scratch, name))
    ]
    if len(entries) != 1:
        raise ValueError(
            "cannot infer the repository directory inside the archive "
            f"(found {sorted(entries)}); pass repo_dir explicitly"
        )
    return os.path.join(scratch, entries[0])


def _purge_artifacts(root: str) -> None:
    """Remove the sandbox's git metadata and build artifacts, then make the
    tree readable/writable so the overlay cannot fail on sandbox modes."""
    for dirpath, dirnames, _ in os.walk(root, topdown=True):
        for name in list(dirnames):
            if name in EXCLUDED_DIRS:
                shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)
                dirnames.remove(name)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            path = os.path.join(dirpath, name)
            try:
                os.chmod(path, os.stat(path).st_mode | 0o600)
            except OSError:
                pass


def _overlay(source_root: str, clone: str) -> None:
    for name in os.listdir(source_root):
        if name in EXCLUDED_DIRS:
            continue
        src = os.path.join(source_root, name)
        dst = os.path.join(clone, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)