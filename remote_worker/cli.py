"""Command line entry point.

Thin on purpose: parse, validate the cheap things, delegate to
:class:`remote_worker.runner.TaskRunner`, report in plain language.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import planner

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
    parser.add_argument("--version", action="store_true",
                        help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="delegate one task")
    run.add_argument("task", help="the task, in plain language")
    run.add_argument("--repo", required=True, help="repository URL to mount")
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

    status = sub.add_parser("status", help="show one task or all of them")
    status.add_argument("task_id", nargs="?")

    cont = sub.add_parser("continue", help="resume a task that hit its budget")
    cont.add_argument("task_id")
    cont.add_argument("--budget", type=int, default=None)

    cancel = sub.add_parser("cancel", help="stop tracking a task")
    cancel.add_argument("task_id")

    ask = sub.add_parser("ask", help="delegate a non-code task, get the text")
    ask.add_argument("task", help="the task, in plain language")
    ask.add_argument("--model", default=None,
                     help=f"one of: {', '.join(planner.ALLOWED_MODELS)}")
    ask.add_argument("--budget", type=int, default=None)
    ask.add_argument("--stream", action="store_true",
                     help="stream the worker's progress live")

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

    return parser


def main(argv=None, *, runner=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if "--version" in argv:
        print(read_version())
        return 0

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if runner is None:
        runner = _build_runner(parser, args)

    try:
        if args.command == "ask":
            from .config import load_config

            stored = load_config()
            model = args.model or stored["model"]
            budget = args.budget or stored["budget"]
            _check_model(parser, model, budget)
            return _report_ask(runner.ask(
                task=args.task, model=model, budget=budget,
                stream=getattr(args, "stream", False),
            ))
        if args.command == "run":
            from .config import load_config

            stored = load_config()
            model = args.model or stored["model"]
            budget = args.budget or stored["budget"]
            _check_model(parser, model, budget)
            return _report_run(runner.run(
                task=args.task, repo_url=args.repo, model=model,
                budget=budget, branch=args.branch,
            ))
        if args.command == "status":
            return _report_status(runner.status(args.task_id))
        if args.command == "continue":
            if args.budget is not None:
                _check_model(parser, planner.DEFAULT_MODEL, args.budget)
            return _report_run(runner.continue_task(
                task_id=args.task_id, budget=args.budget,
            ))
        if args.command == "setup":
            from .config import save_config
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
            path = save_config(model=args.model, budget=args.budget)
            print(f"wrote {path}")
            return 0
        if args.command == "cancel":
            task = runner.cancel(args.task_id)
            print(f"canceled: {task['task_id']} (local tracking only)")
            return 0
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 0


def _check_model(parser, model, budget) -> None:
    """Fail as a usage error, before any network call."""
    try:
        planner.agent_config(model=model, budget=budget)
    except ValueError as exc:
        parser.error(str(exc))


def _build_runner(parser, args):
    from .runner import TaskRunner

    api_key = os.environ.get("GEMINI_API_KEY", "")
    needs_key = getattr(args, "command", None) in ("run", "ask", "continue")
    if needs_key and not api_key:
        parser.error("GEMINI_API_KEY is not set")
    project = getattr(args, "project", None) or _guess_project(args)
    return TaskRunner(project=project, api_key=api_key)


def _guess_project(args) -> str:
    """Default to the directory the user runs in, not the remote repo name.

    Engram identifies a project by the working directory; the repo mounted in
    the sandbox is usually a *different* checkout and rarely a match.
    """
    return os.path.basename(os.getcwd()) or "default"


def _report_run(result: dict) -> int:
    status = result.get("status")
    task_id = result.get("task_id")
    print(f"task:   {task_id}")
    print(f"status: {status}")

    tokens = result.get("tokens")
    budget = result.get("budget")
    if tokens is not None:
        suffix = f" of {budget}" if budget else ""
        print(f"tokens: {tokens}{suffix}")

    if status == "incomplete":
        print(
            "the agent reached its token budget; the task is incomplete, "
            "not failed."
        )
        print(f"resume it with: remote-worker continue {task_id}")
        return 0

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
    if tokens is not None:
        suffix = f" of {budget}" if budget else ""
        print(f"tokens: {tokens}{suffix}")
    if status == "incomplete":
        print("the agent reached its token budget; the task is incomplete.")
        print(f"resume it with: remote-worker continue {task_id}")
        return 0
    if status != "completed":
        print(result.get("text") or result.get("report") or "")
        return 3
    print()
    print(result.get("text", ""))
    return 0


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