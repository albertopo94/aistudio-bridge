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
import datetime
import io
import os
import shutil
import subprocess
import tarfile
import tempfile

#: Documented cap for a ``repository`` source (docs/agent-environment).
REPOSITORY_SOURCE_LIMIT_BYTES = 500 * 1024 * 1024

# The direct upload route deliberately has NO local ceiling constant here. Its
# limit is the account's fair-use window over a time span, which no constant
# can know: 1530 MiB passed on fresh quota and 300 MiB failed hours later. The
# service decides and remote_worker.routes explains its refusal.

#: Inline sources are capped at 1 MB per file and 2 MB in total by the
#: environment documentation. Enforcing that here turns a request the service
#: would reject into an explanation the user can act on.
INLINE_FILE_LIMIT_BYTES = 1 * 1024 * 1024
INLINE_TOTAL_LIMIT_BYTES = 2 * 1024 * 1024

#: Ceiling for the total declared size of an extracted workspace tar. The
#: biggest workspace ever delivered here was 3.6 GiB (odd L39); 4 GiB bounds a
#: hostile archive that claims member sizes to fill the disk.
EXTRACT_TOTAL_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


class UnsafeTarMemberError(tarfile.TarError):
    """A workspace member that must never touch the disk (hardening S2).

    The delivery unpacks a tree an agent built inside a sandbox the prompt
    may have contaminated. This error is a ``tarfile.TarError``, so the
    run path already maps it to ``undelivered`` — the branch is never
    half-written, and the sandbox stays the only copy for recovery.
    """


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

    Returns ``{"path", "bytes", "files", "base_commit"}``. ``base_commit``
    is the repository HEAD the snapshot was packed from, so the delivery can
    be anchored on the exact point Antigravity received instead of the remote
    line that may have advanced meanwhile (P0-1).
    """
    if not os.path.isdir(repo_path):
        raise ValueError(f"local repository {repo_path!r} is not a directory")
    base_commit = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
    if not base_commit:
        raise ValueError(
            f"local repository {repo_path!r} has no commit to snapshot"
        )
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
    return {"path": dest, "bytes": os.path.getsize(dest),
            "files": written, "base_commit": base_commit}


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


def mount_gcs(uri: str, target: str) -> dict:
    """Build the ``gcs`` source entry for an environment.

    The service expects ``{"type": "gcs", "source": "gs://bucket/prefix",
    "target": "/workspace/..."}`` and COPIES the tree into the sandbox at
    mount time (docs/agent-environment); it is not a live mount. The
    documented cap is 2 GB per gcs source, and target ``"/"`` is not
    allowed. A uri that is not ``gs://`` or that has an empty bucket is
    refused here with the exact problem, before any request.
    """
    if not uri or not uri.startswith("gs://"):
        raise ValueError(f"a GCS source must be a gs:// uri, got {uri!r}")
    rest = uri[len("gs://"):]
    bucket = rest.split("/", 1)[0]
    if not bucket:
        raise ValueError(f"a GCS source needs a bucket, got {uri!r}")
    if not target or not target.startswith("/") or target == "/":
        raise ValueError(
            f"target must be an absolute path inside the sandbox (not the "
            f"root), got {target!r}"
        )
    return {"type": "gcs", "source": uri, "target": target}


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


def _workspace_paths(source_root: str) -> list[str]:
    """Every file path the delivered workspace holds, clone-relative.

    Staging these EXACT paths is what keeps the delivery honest (S9): the
    commit holds the workspace and nothing else. A user's scratch file in the
    clone -- at the root or inside a tracked directory -- is never part of it,
    and a file the workspace does not hold is already deleted by
    :func:`_drop_removed`, so `git add` records the removal.
    """
    paths = []
    for name in os.listdir(source_root):
        if name in EXCLUDED_DIRS:
            continue
        top = os.path.join(source_root, name)
        if os.path.isdir(top):
            for root, dirs, files in os.walk(top):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                for one in files:
                    full = os.path.join(root, one)
                    paths.append(os.path.relpath(full, source_root))
        else:
            paths.append(name)
    return sorted(paths)


def apply_to_branch(*, tar_path: str, repo_url: str, branch: str,
                    workdir: str, repo_dir: str | None = None,
                    base_commit: str | None = None) -> dict:
    """Apply a downloaded workspace tar onto a fresh clone, on a new branch.

    "Fresh" is load-bearing twice. Across repositories: the clone path is
    per project and outlives a task, so a clone of ANOTHER repository left
    there by an earlier run must never absorb this delivery -- the branch
    would land on unrelated history while the real repository never
    receives it (T25's live run found exactly that, odd L45); a mismatched
    clone is kept under a dated name (it may hold a branch nobody merged
    yet) and the right one is cloned in its place. Across tasks of the SAME
    repository (hardening S9): the branch is reset to the remote's main
    line and the delivery is committed, so the next task's diff --cached
    can only ever hold its own work.

    ``base_commit`` pins the delivery to the snapshot the task actually
    sent (P0-1). Without it the branch would be reset to the CURRENT remote
    main line, which may have advanced past the point Antigravity saw -- a
    delivery built over the old snapshot would then, through
    :func:`_drop_removed`, delete files the advancing branch added. With it
    the branch is anchored on that exact commit and ``base_moved``
    reports (as a reconciliation signal, not an error) when the live remote
    main is no longer the recorded snapshot.

    Returns ``{"clone_dir", "diff", "changed", "base_moved"}``.
    ``changed`` lists the ``git status --short`` lines taken before the
    commit so the caller can report what moved; the index is clean
    afterwards. ``base_moved`` is True only when ``base_commit`` was given
    and the remote main line is not that commit.
    """
    clone = os.path.abspath(workdir)
    if not _clone_of(clone, repo_url):
        if os.path.isdir(clone):
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S-%f")
            os.replace(clone, f"{clone}.bak-{stamp}")
        _git(None, "clone", "-q", repo_url, clone)
    try:
        _git(clone, "fetch", "-q", "origin")
    except GitError:
        pass  # offline: the cached remote refs still bound the main line
    base = base_commit or _remote_base(clone)
    remote = _remote_base(clone)
    # The branch moved when the remote's main line is no longer the exact
    # snapshot the task was built from. Resolve the live ref to its commit
    # first (``_is_ancestor`` would be the wrong test: the snapshot is always
    # an ancestor of the advanced line).
    base_moved = False
    if base_commit and remote:
        live = _git(clone, "rev-parse", remote).stdout.strip()
        base_moved = live != base_commit
    _git(clone, "checkout", "-q", "-B", branch, *([base] if base else []))

    with tempfile.TemporaryDirectory(prefix="worker-tar-") as scratch:
        _extract(tar_path, scratch)
        source_root = _repo_root(scratch, repo_dir)
        _purge_artifacts(source_root)
        _drop_removed(clone, source_root)
        _overlay(source_root, clone)
        pathspecs = _workspace_paths(source_root)

    if pathspecs:
        # No `-A`, no directory pathspec: git would sweep the user's own
        # untracked files (at the root or inside a tracked directory) into
        # the delivery commit. Staging the exact workspace paths keeps the
        # commit honest (S9).
        _git(clone, "add", "-f", "--", *pathspecs)
    # Files the workspace no longer holds were removed from the working
    # tree by _drop_removed but are not covered by _workspace_paths (they
    # do not exist on disk); stage them by name so the deletion lands.
    for rel in _git(clone, "ls-files").stdout.splitlines():
        if not os.path.lexists(os.path.join(clone, rel)):
            _git(clone, "add", "-f", "-A", "--", rel)
    diff = _git(clone, "diff", "--cached").stdout
    changed = [
        line for line in _git(clone, "status", "--short").stdout.splitlines()
        # "??" is the user's own untracked residue in the clone; staging by
        # pathspec kept it out of the delivery, reporting it would lie.
        if line.strip() and not line.startswith("??")
    ]
    if changed:
        _git(clone, "commit", "-q", "-m",
             f"remote-worker delivery on {branch}")
    return {"clone_dir": clone, "diff": diff, "changed": changed,
            "base_moved": base_moved}


def _remote_base(clone: str) -> str | None:
    """The remote's main line as a local ref, or None if none resolves."""
    for ref in ("origin/HEAD", "origin/main", "origin/master"):
        proc = subprocess.run(
            ["git", "-C", clone, "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return ref
    return None


def _drop_removed(clone: str, source_root: str) -> None:
    """Delete tracked files the delivered workspace no longer holds (S4).

    The overlay only ever adds; without this, an agent that deletes a file
    delivers a branch that silently keeps it. The removal runs before the
    overlay against the files tracked at the checked-out base, and only
    candidates tracked there are touched -- so the user's own untracked
    files in the clone survive the delivery.
    """
    for rel in _git(clone, "ls-files").stdout.splitlines():
        if rel.split("/")[0] in EXCLUDED_DIRS:
            continue
        if os.path.lexists(os.path.join(source_root, rel)):
            continue
        target = os.path.join(clone, rel)
        if os.path.islink(target) or os.path.isfile(target):
            os.remove(target)
        elif os.path.isdir(target):
            shutil.rmtree(target)


#: The only environment variables a test child inherits (hardening S1).
#: Everything else -- API keys, tokens, SSH agent, cloud credentials -- stays
#: with the parent: the code under test was written by a prompted agent, and
#: a directory name is not a sandbox.
TEST_ENV_ALLOWLIST = frozenset({
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "TERM", "SHELL",
})


def test_env(home: str) -> dict[str, str]:
    """The reduced environment a delivered project's tests run in.

    Allowlist, never a blocklist: a variable nobody thought of cannot leak.
    ``HOME`` is pointed at the scratch directory so the common dotfile paths
    (``~/.ssh``, ``~/.aws``, a dotfile holding a token) are not named by the
    default ``HOME``. This is a reduction, **not a sandbox**: the child runs
    as the same macOS user, can read any file by absolute path, and is given
    no network restriction.
    """
    env = {
        name: os.environ[name]
        for name in TEST_ENV_ALLOWLIST
        if name in os.environ
    }
    env["HOME"] = home
    # The user reviews the clone's git diff after the tests run, so the test
    # run must not add build artifacts that would appear as untracked changes.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_tests(workdir: str, command: list[str],
              timeout: float = 1800.0,
              env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run the project's own test command inside the clone.

    Returns ``(returncode, combined_output)``. A non-zero status is a result,
    not an exception: the caller reports it.

    The child gets the reduced environment by default (hardening S1; see
    :func:`test_env` for what it does and does not isolate); pass ``env``
    only for a caller that means to hand it specific variables.
    """
    child_env = test_env(os.path.abspath(workdir)) if env is None else env
    try:
        proc = subprocess.run(
            command, cwd=workdir, capture_output=True, text=True,
            timeout=timeout, env=child_env,
        )
    except subprocess.TimeoutExpired:
        return 124, f"test command timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


# --- internals ----------------------------------------------------------

def _clone_of(clone: str, repo_url: str) -> bool:
    """True only when this directory is a working copy of that exact repo."""
    if not os.path.isdir(os.path.join(clone, ".git")):
        return False
    proc = subprocess.run(["git", "-C", clone, "remote", "get-url",
                           "origin"], capture_output=True, text=True)
    if proc.returncode != 0:
        return False

    def norm(value: str) -> str:
        value = value.strip()
        for suffix in (".git", "/"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
        return os.path.realpath(value) if os.path.isabs(value) else value

    return norm(proc.stdout) == norm(repo_url)


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


def _safe_member_path(dest: str, name: str) -> str:
    """Resolve one tar member inside ``dest`` or refuse it (hardening S2).

    Rejects absolute paths (POSIX, Windows drive, UNC) and any ``..``
    component, then re-checks the resolved path against the destination
    after normalization -- so ``a/../../x`` is refused even when its first
    segment looked harmless.
    """
    flat = name.replace("\\", "/")
    if flat.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        raise UnsafeTarMemberError(f"absolute member path: {name!r}")
    if ".." in flat.split("/"):
        raise UnsafeTarMemberError(f"member escapes the destination: {name!r}")
    target = os.path.normpath(os.path.join(dest, *flat.split("/")))
    root = os.path.normpath(dest)
    if target != root and not target.startswith(root + os.sep):
        raise UnsafeTarMemberError(f"member escapes the destination: {name!r}")
    return target


def _extract_manual(tar: tarfile.TarFile, dest: str, members: list) -> None:
    """Extract pre-validated regular members on a Python without ``filter``.

    Hardening S2 forbade the historical fallback (``extractall(dest)`` with
    no filter) that silently dropped every protection on Python older than
    the Sept-2023 patches. Every member here already passed :func:
   :`_safe_member_path`, the size cap, and the file/dir type check, and each
    stream is capped at its declared size, so this path stays fail-closed.
    """
    for member in members:
        target = _safe_member_path(dest, member.name)
        if member.isdir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:  # only for link-like members, refused above
            raise UnsafeTarMemberError(f"unreadable member: {member.name!r}")
        remaining = member.size
        with source, open(target, "wb") as out:
            while remaining > 0:
                chunk = source.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise UnsafeTarMemberError(
                        f"member is shorter than its declared size: "
                        f"{member.name!r}"
                    )
                out.write(chunk)
                remaining -= len(chunk)
        os.chmod(target, 0o644)


def _extract(tar_path: str, dest: str) -> None:
    """Unpack a workspace tar into ``dest``, refusing unsafe members (S2).

    Validation runs over every header BEFORE a single byte is written: a
    hostile archive aborts the delivery instead of leaving a partial tree.
    The `data` filter (Python >= 3.12, backported to the Sept-2023 patch
    releases) stays as a second net on modern interpreters; on an older
    one the declared floor (3.10) the pre-validation plus the capped
    manual extraction above replace it -- never an unfiltered extractall.
    """
    with tarfile.open(tar_path) as tar:
        members = tar.getmembers()
        total = 0
        for member in members:
            if not (member.isfile() or member.isdir()):
                raise UnsafeTarMemberError(
                    f"{member.type.decode(errors='replace')!r} member is not "
                    f"allowed in a workspace: {member.name!r}"
                )
            total += member.size
            if total > EXTRACT_TOTAL_LIMIT_BYTES:
                raise UnsafeTarMemberError(
                    f"workspace declares more than "
                    f"{EXTRACT_TOTAL_LIMIT_BYTES // (1024 * 1024)} MB "
                    f"(at member {member.name!r})"
                )
            _safe_member_path(dest, member.name)
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python without the filter argument (< 3.12)
            _extract_manual(tar, dest, members)


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