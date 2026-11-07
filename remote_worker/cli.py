"""Command line entry point.

Thin on purpose: parse, validate the cheap things, delegate to
:class:`remote_worker.runner.TaskRunner`, report in plain language.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import planner
from . import routes
from .routes import alternatives_line, human_bytes, human_duration, human_rate
from .google_client import InteractionsError

VERSION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"
)


def read_version() -> str:
    try:
        with open(VERSION_PATH, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remote-worker",
        description=(
            "Delegate a bounded code task to Google's Antigravity agent and "
            "bring the result back to a local branch."
        ),
    )
    parser.add_argument("--version", "-v", action="store_true",
                        help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="delegate one task")
    run.add_argument("task", help="the task, in plain language")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", help="repository URL to mount")
    source.add_argument(
        "--local-repo", metavar="PATH",
        help="send a local working tree instead of a repository URL: it is "
             "archived and uploaded into the sandbox (no git history "
             "travels), which is the only way a local-only or oversized "
             "repository can reach it",
    )
    source.add_argument(
        "--gcs-source", metavar="gs://BUCKET/PREFIX",
        help="mount a Cloud Storage bucket as the sandbox's repository: the "
             "service COPIES the tree out of your bucket at mount time (up to "
             "2 GB per source, documented), so no bytes leave your machine "
             "over the upload path",
    )
    run.add_argument(
        "--gcs-token-file", default=None, metavar="PATH",
        help="read the GCS bearer token from this file instead of asking "
             "gcloud; the value is never printed",
    )
    run.add_argument(
        "--delete-env", action="store_true",
        help="delete the sandbox once the delivery is verified (the branch "
             "exists). A failed delivery always keeps it: the sandbox is "
             "then the only copy of the work",
    )
    run.add_argument("--model", default=None,
                     help=f"one of: {', '.join(planner.ALLOWED_MODELS)} "
                          "(default: stored config)")
    run.add_argument("--budget", type=int, default=None,
                     help="max total tokens for the task "
                          "(default: stored config)")
    run.add_argument("--branch", default=None,
                     help="local branch name for the result")
    run.add_argument("--project", default=None,
                     help="Engram project (defaults to the repo name)")
    run.add_argument("--background", action="store_true",
                     help="run the task on the service and poll for it, so a "
                          "dropped connection does not lose the result")
    run.add_argument("--allow-domain", action="append", default=None,
                     metavar="DOMAIN",
                     help="limit the sandbox's outbound traffic to this domain "
                          "(repeatable); without it the sandbox reaches any "
                          "host")
    run.add_argument("--no-network", action="store_true",
                     help="give the sandbox no outbound network at all")
    run.add_argument("--reuse-env", default=None, metavar="ENVIRONMENT_ID",
                     help="run inside an existing sandbox instead of a fresh "
                          "one, keeping the dependencies an earlier task "
                          "installed")
    run.add_argument("--agents-file", default=None, metavar="PATH",
                     help="send this local AGENTS.md to /.agents/AGENTS.md, "
                          "which the service loads as system instructions")
    run.add_argument("--mount-file", action="append", default=None,
                     metavar="LOCAL:TARGET",
                     help="place a local text file at TARGET inside the "
                          "sandbox (repeatable), e.g. a skill at "
                          "/.agents/skills/<name>/SKILL.md")
    run.add_argument("--image", action="append", default=None, metavar="PATH",
                     help="send an image with the task (repeatable): png, "
                          "jpeg, gif or webp")

    status = sub.add_parser("status", help="show one task or all of them")
    status.add_argument("task_id", nargs="?")
    status.add_argument("--live", action="store_true",
                        help="also ask the service for the live status of one "
                             "task (needs an API key)")

    cont = sub.add_parser("continue", help="resume a task that hit its budget")
    cont.add_argument("task_id")
    cont.add_argument("--budget", type=int, default=None)

    cancel = sub.add_parser("cancel", help="stop tracking a task")
    cancel.add_argument("task_id")

    clean = sub.add_parser("clean",
                           help="list sandboxes still held by your account")
    clean.add_argument("--yes", action="store_true",
                       help="actually delete them; without this flag nothing "
                            "is removed")

    sub.add_parser(
        "routes", help="print every way code can reach the sandbox and come "
                       "back from it, with measured limits, so you can pick "
                       "one")

    fetch = sub.add_parser(
        "fetch", help="read what a task left in its sandbox: list it, or take "
                      "one file (run already brings the whole workspace back)")
    fetch.add_argument("task_id")
    fetch.add_argument("--list", action="store_true",
                       help="list the entries under workspace (or under "
                            "--path) without downloading anything")
    fetch.add_argument("--path", default=None, metavar="PATH",
                       help="without --list: this one file back from the "
                            "sandbox; with --list: list under this path "
                            "instead of workspace")
    fetch.add_argument("--out", default=None, metavar="DEST",
                       help="where to write it (default: the current "
                            "directory); an existing file is left untouched "
                            "unless --overwrite")
    fetch.add_argument("--overwrite", action="store_true",
                       help="replace the local file at --out if there is one")

    sub.add_parser(
        "probe", help="upload 1 MiB to a scratch sandbox and report whether "
                      "this account can upload right now (takes seconds, "
                      "costs no task quota)")

    ask = sub.add_parser("ask", help="delegate a non-code task, get the text")
    ask.add_argument("task", help="the task, in plain language")
    ask.add_argument("--model", default=None,
                     help=f"one of: {', '.join(planner.ALLOWED_MODELS)}")
    ask.add_argument("--budget", type=int, default=None)
    ask.add_argument("--stream", action="store_true",
                     help="stream the worker's progress live")
    ask.add_argument("--background", action="store_true",
                     help="run the task on the service and poll for it, so a "
                          "dropped connection does not lose the result")
    ask.add_argument("--image", action="append", default=None,
                     metavar="PATH",
                     help="send an image with the task (repeatable): png, "
                          "jpeg, gif or webp")

    setup = sub.add_parser("setup", help="store defaults and install the skill")
    setup.add_argument("--model", default=None,
                       help=f"one of: {', '.join(planner.ALLOWED_MODELS)}")
    setup.add_argument("--budget", type=int, default=None)
    setup.add_argument("--install-skill", action="store_true",
                       help="write the remote-worker skill into the agent config")
    setup.add_argument("--restore-skill", action="store_true",
                       help="restore the skill from its most recent backup")
    setup.add_argument("--list-skill-backups", action="store_true",
                       help="list the skill backups")
    setup.add_argument("--api-key", default=None,
                       help="store the Gemini API key (interactive prompt if omitted)")

    return parser


def main(argv=None, *, runner=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if "--version" in argv or "-v" in argv:
        print(read_version())
        return 0

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "routes":
        # Offline on purpose: the table is what you read to decide whether to
        # spend quota at all, so reading it must never need a key or a call.
        print(routes.format_routes())
        print()
        print(routes.format_return_routes())
        return 0

    if runner is None:
        runner = _build_runner(parser, args)

    try:
        if args.command == "ask":
            from .config import load_config

            if args.stream and args.background:
                # Streaming reads the answer off one open connection; the
                # whole point of background is that no connection is held. The
                # combination has no documented meaning, so it is refused
                # rather than silently resolved by preferring one of them.
                parser.error("--stream and --background cannot be combined: "
                             "pick --stream to watch it live, or --background "
                             "to keep the result across a dropped "
                             "connection")
            stored = load_config()
            model = args.model or stored["model"]
            budget = args.budget or stored["budget"]
            _check_model(parser, model, budget)
            try:
                images = _check_images(args.image)
            except ValueError as exc:
                parser.error(str(exc))
            return _report_ask(runner.ask(
                task=args.task, model=model, budget=budget,
                stream=getattr(args, "stream", False),
                background=args.background,
                images=images,
            ))
        if args.command == "run":
            from .config import load_config

            stored = load_config()
            model = args.model or stored["model"]
            budget = args.budget or stored["budget"]
            _check_model(parser, model, budget)
            try:
                planner.network_config(allow_domains=args.allow_domain,
                                       no_network=args.no_network)
            except ValueError as exc:
                parser.error(str(exc))
            for missing in planner.wildcard_gaps(args.allow_domain):
                print(f"note: '*.{missing}' does not match the root domain "
                      f"'{missing}'; add --allow-domain {missing} if you "
                      "need it", file=sys.stderr)
            try:
                mount_files = _split_mount_files(args.mount_file)
                images = _check_images(args.image)
            except ValueError as exc:
                parser.error(str(exc))
            if args.reuse_env is not None and not args.reuse_env.strip():
                parser.error("--reuse-env needs an environment id "
                             "('remote-worker clean' lists what you hold)")
            if args.local_repo is not None and args.reuse_env is not None:
                parser.error("--local-repo builds a fresh sandbox, so it "
                             "cannot be combined with --reuse-env")
            if args.gcs_source is not None and args.reuse_env is not None:
                parser.error("--gcs-source builds a fresh sandbox, so it "
                             "cannot be combined with --reuse-env (a reused "
                             "sandbox already has its own sources and "
                             "network rules)")
            if args.no_network and args.gcs_source is not None \
                    and args.gcs_token_file is not None:
                parser.error("--gcs-source with a token cannot be combined "
                             "with --no-network")
            result = runner.run(
                task=args.task, repo_url=args.repo, model=model,
                budget=budget, branch=args.branch,
                background=args.background,
                allow_domains=args.allow_domain,
                no_network=args.no_network,
                reuse_env=args.reuse_env,
                agents_file=args.agents_file,
                mount_files=mount_files,
                images=images,
                local_repo=args.local_repo,
                delete_env=args.delete_env,
                gcs_source=args.gcs_source,
                gcs_token_file=args.gcs_token_file,
            )
            code = _report_run(result)
            environment_id = result.get("environment_id")
            if args.delete_env and result.get("env_release_error"):
                # The delivery stands; the delete is the part that failed.
                print(f"could not delete sandbox {environment_id}: "
                      f"{result['env_release_error']}", file=sys.stderr)
            if (not args.delete_env and result.get("status") == "completed"
                    and environment_id):
                _offer_sandbox_release(runner, environment_id)
            return code
        if args.command == "clean":
            return _report_clean(runner.clean(apply=args.yes))
        if args.command == "fetch":
            if not args.list and not args.path:
                parser.error("fetch needs --list to see what the sandbox holds "
                             "or --path FILE to bring one file back "
                             "(remote-worker fetch --help)")
            if args.out and not args.path:
                parser.error("--out needs --path: there is nothing to write "
                             "in a plain --list")
            if args.list:
                return _report_fetch(
                    runner.list_workspace(args.task_id,
                                          path=args.path or "workspace"),
                    listing=True)
            dest = args.out
            if dest is None:
                dest = os.path.join(os.getcwd(),
                                    os.path.basename(args.path.strip("/")))
            return _report_fetch(
                runner.fetch_file(args.task_id, args.path, dest,
                                  overwrite=args.overwrite),
                listing=False)
        if args.command == "probe":
            return _report_probe(runner.probe())
        if args.command == "status":
            code = _report_status(runner.status(args.task_id))
            if args.live:
                if not args.task_id:
                    parser.error("--live needs a task id")
                _report_live_status(runner, args.task_id)
            return code
        if args.command == "continue":
            if args.budget is not None:
                _check_model(parser, planner.DEFAULT_MODEL, args.budget)
            return _report_run(runner.continue_task(
                task_id=args.task_id, budget=args.budget,
            ))
        if args.command == "setup":
            from .config import load_config, save_config
            from .skill_installer import install_skill, restore_skill, list_backups

            if args.list_skill_backups:
                for b in list_backups():
                    print(b)
                return 0
            if args.install_skill:
                print("wrote", install_skill())
                return 0
            if args.restore_skill:
                print("restored", restore_skill())
                return 0
            _check_model(parser, args.model, args.budget)
            api_key = args.api_key or _prompt_for_key(parser)
            path = save_config(model=args.model, budget=args.budget,
                               api_key=api_key)
            print(f"wrote {path}")
            if api_key:
                print("api key stored (file is private: 0600)")
            else:
                existing = load_config().get("api_key")
                if existing:
                    print("api key: kept the previously stored one")
            return 0
        if args.command == "cancel":
            task = runner.cancel(args.task_id)
            if task.get("remote_canceled"):
                print(f"canceled: {task['task_id']} "
                      "(stopped through the service)")
            else:
                print(f"canceled: {task['task_id']} (local tracking only)")
                if task.get("remote_error"):
                    print("the service refused the cancel; the interaction "
                          "may still run and spend quota")
            return 0
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except InteractionsError as exc:
        return _report_api_error(exc)

    parser.print_help()
    return 0


def _prompt_for_key(parser) -> str | None:
    """Ask for the API key once, hidden, when none is stored yet."""
    import getpass

    from .config import load_config

    if load_config().get("api_key"):
        return None  # keep it; do not ask again
    print("No stored API key. Paste your Gemini API key (input is hidden).")
    key = getpass.getpass("API key: ").strip()
    if not key:
        return None
    return key


def _check_model(parser, model, budget) -> None:
    """Fail as a usage error, before any network call."""
    try:
        planner.agent_config(model=model, budget=budget)
    except ValueError as exc:
        parser.error(str(exc))


def _build_runner(parser, args):
    from .runner import TaskRunner

    from .config import resolve_api_key

    needs_key = getattr(args, "command", None) in ("run", "ask", "continue",
                                                   "clean", "probe", "fetch")
    wants_live = (getattr(args, "command", None) == "status"
                  and getattr(args, "live", False))
    api_key = resolve_api_key() or ""
    if (needs_key or wants_live) and not api_key:
        parser.error(
            "no API key found: set GEMINI_API_KEY or run "
            "'remote-worker setup' once"
        )
    project = getattr(args, "project", None) or _guess_project(args)
    return TaskRunner(project=project, api_key=api_key)


def _guess_project(args) -> str:
    """Default to the directory the user runs in, not the remote repo name.

    Engram identifies a project by the working directory; the repo mounted in
    the sandbox is usually a *different* checkout and rarely a match.
    """
    return os.path.basename(os.getcwd()) or "default"


def _check_images(values):
    """Existence is checked here, content type further down.

    A missing path is a typo the user can fix immediately, so it is a usage
    error before any request. Whether the bytes are an image the agent can
    read is decided by the part builder, which is the one place that knows the
    accepted signatures.
    """
    for path in values or []:
        if not os.path.isfile(path):
            raise ValueError(f"no such image file: {path}")
    return list(values) if values else None


def _split_mount_files(values):
    """Turn ``LOCAL:TARGET`` strings into (local, target) pairs.

    Windows drive letters are not a concern here (the product targets macOS
    and the target is always an absolute sandbox path), so the first colon
    splits the pair and anything after it belongs to the target.
    """
    pairs = []
    for value in values or []:
        local, sep, target = (value or "").partition(":")
        if not sep or not local.strip() or not target.strip():
            raise ValueError(
                f"--mount-file wants LOCAL:TARGET, got {value!r}"
            )
        pairs.append((local.strip(), target.strip()))
    return pairs


def _offer_sandbox_release(runner, environment_id: str) -> None:
    """Ask whether to release the sandbox of a delivered run (T25/L42).

    Only a delivered run gets here, so the branch already has the work and
    deleting is safe. The default is KEEP, and an answer is only taken from
    a terminal: an agent driving the CLI (stdin is a pipe) gets the question
    and the command that answers it later, which is the same ask with the
    same default.
    """
    if not sys.stdin.isatty():
        print(f"sandbox {environment_id} is kept for 7 days; to release "
              f"finished sandboxes: remote-worker clean")
        return
    try:
        answer = input(f"sandbox {environment_id} is kept for 7 days -- "
                       f"delete it now? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        return
    try:
        runner.environments.delete_environment(environment_id)
        print(f"deleted sandbox {environment_id}")
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"could not delete sandbox {environment_id}: {exc}",
              file=sys.stderr)


def _report_run(result: dict) -> int:
    status = result.get("status")
    task_id = result.get("task_id")
    print(f"task:   {task_id}")
    print(f"status: {status}")

    tokens = result.get("tokens")
    budget = result.get("budget")
    if tokens is not None and budget:
        print(f"tokens: {tokens} of {budget}")
    elif tokens is not None:
        print(f"tokens: {tokens}")

    if status == "incomplete":
        reason = result.get("reason")
        if reason in ("running", "watch"):
            # Not a budget stop: saying so, and printing a `continue` we just
            # refused to run, would send the user in circles.
            print(result.get("report") or
                  "the interaction is still running.")
            return 0
        print(
            "the agent reached its token budget; the task is incomplete, "
            "not failed."
        )
        print(f"resume it with: remote-worker continue {task_id}")
        return 0

    if status == "undelivered":
        # The agent finished and spent its budget; the bytes never arrived.
        # Printing only the raw service error (as before) left the user with a
        # paid-for task and no way out, when the work is sitting in the
        # sandbox reachable file by file (F1).
        print(result.get("report") or "")
        print(f"delivery: failed -- the work is in the sandbox "
              f"{result.get('environment_id')}, but it could not be brought "
              f"back:\n  {result.get('delivery_error')}", file=sys.stderr)
        print("the task itself is done and paid for, so it is not continued; "
              "the sandbox holds the files for the rest of its 7-day life. "
              "Recover them with:", file=sys.stderr)
        print(f"  remote-worker fetch {task_id} --list", file=sys.stderr)
        print(f"  remote-worker fetch {task_id} --path <file>",
              file=sys.stderr)
        print("no branch was created, so there is nothing to merge; and "
              "`clean` keeps this sandbox while the task reads undelivered",
              file=sys.stderr)
        return 3

    if status != "completed":
        report = result.get("report")
        if report:
            print(report)
        return 3

    branch = result.get("branch")
    if branch:
        print(f"branch: {branch}")
    clone_dir = result.get("clone_dir")
    if clone_dir:
        print(f"clone:  {clone_dir}")
    for line in result.get("changed") or []:
        print(f"  {line}")
    tests_rc = result.get("tests_rc")
    if tests_rc is not None:
        verdict = "pass" if tests_rc == 0 else f"FAIL (rc {tests_rc})"
        print(f"tests:  {verdict}")
    print("nothing was pushed; merge the branch when you are ready.")
    return 0 if tests_rc in (None, 0) else 1


def _report_ask(result: dict) -> int:
    status = result.get("status")
    task_id = result.get("task_id")
    print(f"task:   {task_id}")
    print(f"status: {status}")
    tokens = result.get("tokens")
    budget = result.get("budget")
    if tokens is not None and budget:
        print(f"tokens: {tokens} of {budget}")
    elif tokens is not None:
        print(f"tokens: {tokens}")
    if status == "incomplete":
        if result.get("reason") in ("running", "watch"):
            print(result.get("text") or result.get("report") or "")
            return 0
        print("the agent reached its token budget; the task is incomplete.")
        print(f"resume it with: remote-worker continue {task_id}")
        return 0
    if status != "completed":
        print(result.get("text") or result.get("report") or "")
        return 3
    print()
    print(result.get("text", ""))
    return 0


def _report_api_error(exc) -> int:
    """Turn an API error into advice, never into a traceback (G19).

    A 429 gets the one thing the raw body does not say: this is a quota
    window, so waiting is the fix, and re-running blindly is the wrong reflex
    -- a continuation carries previous_interaction_id, so an automatic retry
    would fork the conversation and bill the same work twice. That is why the
    client never retries on its own.
    """
    print(f"error: {exc}", file=sys.stderr)
    if exc.status == 429:
        print(
            "rate limit: the free tier quota for this project is spent for "
            "now. Wait for the window to reset and run the command again.",
            file=sys.stderr,
        )
        print(
            "this tool never retries on its own on purpose: a continuation "
            "carries previous_interaction_id, so an automatic retry is not "
            "idempotent -- it forks the conversation and bills the work twice.",
            file=sys.stderr,
        )
        return 4
    return 1


def _report_live_status(runner, task_id: str) -> None:
    """Print what the service says about a recorded task (G18)."""
    try:
        live = runner.live_status(task_id)
    except InteractionsError as exc:
        _report_api_error(exc)
        return
    if live["error"]:
        print(f"live status: unavailable -- {live['error']}")
        return
    interaction = live["interaction"]
    print(f"live status: {interaction.get('status')}")
    tokens = (interaction.get("usage") or {}).get("total_tokens")
    if tokens is not None:
        print(f"live tokens: {tokens}")


def _report_fetch(result: dict, *, listing: bool) -> int:
    """Report a read of the sandbox (S13).

    The sizes come from whatever the service put in the listing: the files
    endpoint documents a metadata object and this tool prints the size field
    when the entry carries one, and says nothing when it does not, rather than
    inventing a number for a field the response did not send.
    """
    if not result.get("ok"):
        environment_id = result.get("environment_id")
        if result.get("expired"):
            print(f"read: expired -- the sandbox {environment_id} is gone "
                  "(7-day TTL) and its files went with it", file=sys.stderr)
            print("nothing to recover here: start a new run with the same "
                  "repo and task, and read the result while the sandbox "
                  "lives", file=sys.stderr)
            return 3
        if result.get("missing_path"):
            # The sandbox is alive and holding files: the path is what is wrong.
            print(f"read: no such path -- {result.get('path')} is not in the "
                  f"sandbox {environment_id}, which is alive and holds:",
                  file=sys.stderr)
            for name in (result.get("present") or ["(nothing listed)"]):
                print(f"  {name}", file=sys.stderr)
            print("list it with `--list`, or point --path at one of the paths "
                  "above; the sandbox is untouched, so nothing needs a re-run",
                  file=sys.stderr)
            return 2
        print(f"read: refused -- the service answered "
              f"{result.get('status')} {result.get('body')}", file=sys.stderr)
        return 1

    if listing:
        entries = result.get("files") or []
        if not entries:
            print(f"nothing under {result.get('path')}")
            return 0
        for entry in entries:
            path = entry.get("path") or entry.get("name") or "?"
            # ``size_bytes`` is what the service answers with, verified live
            # 2026-11-07 over 12 sandboxes; camelCase stays as a fallback for
            # the JSON style the rest of this API sometimes uses. A DIRECTORY
            # entry carries no size, so it prints no size.
            size = entry.get("size_bytes", entry.get("sizeBytes",
                                                     entry.get("size")))
            try:
                size = human_bytes(int(size)) if size is not None else ""
            except (TypeError, ValueError):
                size = ""
            print(f"  {path}" + (f"  {size}" if size else ""))
        print(f"\n{len(entries)} entries under {result.get('path')}; one file "
              "back with `remote-worker fetch "
              f"{result.get('task_id')} --path <path>`")
        return 0

    print(f"wrote: {result['dest']} ({human_bytes(result['bytes'])})")
    return 0


def _report_probe(result: dict) -> int:
    """Answer the one question `probe` exists for: can this account upload now?

    Exit codes: 0 uploads work, 4 the window is spent (the same code a 429
    gets, because it is the same kind of answer -- wait, do not fix), 1
    anything else.
    """
    held = (f"sandbox {result.get('environment_id')} is still yours; "
            "`remote-worker clean --yes` releases it")
    if result.get("ok"):
        print(f"upload: ok -- {human_bytes(result['bytes'])} in "
              f"{human_duration(result['seconds'])} at "
              f"{human_rate(result['rate'])}")
        if result.get("released"):
            print("the scratch sandbox was deleted again")
        else:
            print(f"warning: {held}", file=sys.stderr)
        return 0
    if result.get("blocked"):
        print("upload: blocked -- the account's fair use window for uploads "
              "is spent right now", file=sys.stderr)
        print("this is not a size problem: the same account takes the same "
              "bytes after the window resets. Retry later, or use a route "
              "that does not upload from here: "
              f"{alternatives_line(exclude='direct-upload')}. "
              "`remote-worker routes` prints them all.", file=sys.stderr)
        if not result.get("released"):
            print(f"warning: {held}", file=sys.stderr)
        return 4
    print(f"upload: refused -- the service answered {result.get('status')} "
          f"{result.get('body')}", file=sys.stderr)
    if not result.get("released"):
        print(f"warning: {held}", file=sys.stderr)
    return 1


def _report_clean(result: dict) -> int:
    """Report a sandbox sweep, dry run or real (G2)."""
    kept = result.get("kept_active") or []
    candidates = result.get("candidates") or []
    failed = result.get("failed") or []

    if not result.get("applied"):
        if not candidates:
            print("no sandboxes to clean")
        else:
            for environment_id in candidates:
                print(f"would delete: {environment_id}")
            print(f"\n{len(candidates)} sandbox(es) can be deleted; nothing "
                  "was removed yet -- run again with --yes")
        for environment_id in kept:
            print(f"kept (a task is still using it): {environment_id}")
        return 0

    for environment_id in result.get("deleted") or []:
        print(f"deleted: {environment_id}")
    for environment_id in kept:
        print(f"kept (a task is still using it): {environment_id}")
    for item in failed:
        print(f"error deleting {item['environment_id']}: {item['error']}",
              file=sys.stderr)
    return 1 if failed else 0


def _report_status(payload) -> int:
    if isinstance(payload, list):
        if not payload:
            print("no tasks recorded")
            return 0
        for task in payload:
            print(f"{task.get('task_id')}  {task.get('status')}")
        return 0
    for key in ("task_id", "status", "model", "branch", "tokens",
                "interaction_id", "environment_id"):
        value = payload.get(key)
        if value is not None:
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())