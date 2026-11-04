# aistudio-bridge

Delegate a bounded code task to Google's Antigravity managed agent
(`antigravity-preview-09-2026`, Interactions API). Memory stays on your
machine, the result comes back as a branch with a clean diff, and nothing is
pushed without you.

```
you ──► remote-worker run "task" ──► Google sandbox (agent edits the repo)
                    │                          │
                    │◄──── memory callbacks ───┤  (answered by your Engram)
                    │                          │
                    ▼                          ▼
          local branch + diff  ◄──── tar of the workspace
```

## Why this shape

- **Google does the heavy part.** No local sandbox, no agent harness to
  maintain: code execution, web access and the agent loop are the service's.
- **Memory never leaves the machine.** When the worker needs memory it asks
  through a tool; this process answers from your local Engram store.
- **Your git stays the source of truth.** The worker never pushes. Its work
  arrives as a tarball, is applied to a fresh clone on a new branch, and is
  tested locally before you look at it.

## Requirements

- Python 3.10+ (standard library only — no third-party dependencies)
- macOS 13+ (Intel or Apple Silicon)
- `git` on PATH
- A Gemini API key. The free tier works: environment compute is not billed
  during the agent's preview, and no paid tier was needed for any task in
  `plan-remote-worker/01-evidencia-poc.md`.
- Optional: [Engram](https://github.com/Gentleman-Programming/engram) on
  PATH. Without it, memory tools report "unavailable" and the task still runs.

## Install

```
git clone https://github.com/albertopo94/aistudio-bridge
cd aistudio-bridge
python3 -m pip install -e .
```

Or run it from the checkout without installing:
`python3 -m remote_worker.cli`.

## First run

```bash
export GEMINI_API_KEY="..."          # never committed, never logged
remote-worker setup --model gemini-3.8-flash --budget 250000
remote-worker run "Add a function divide(a, b) to main.py that raises
ValueError on zero, with two tests. Run the test suite." \
  --repo https://github.com/albertopo94/worker-poc-toy
```

Output:

```
task:   t_1793818082
status: completed
tokens: 107105 of 200000
branch: worker/worker-poc-toy-1793818082
clone:  ~/.remote-worker/worker-poc-toy/work/clone
  M  main.py
  M  tests/test_main.py
tests:  pass
nothing was pushed; merge the branch when you are ready.
```

Then review and merge with your own git:

```bash
cd ~/.remote-worker/<project>/work/clone
git diff main
```

## Commands

| Command | What it does |
|---|---|
| `setup` | Store the default model and budget (`0600`, previous file backed up) |
| `setup --install-skill` | Install the remote-worker skill for gentle-shell-compatible agents |
| `setup --list-skill-backups` | List recoverable versions of the skill |
| `setup --restore-skill` | Restore the skill from its most recent backup (factory reset if it was the zero state) |
| `run "<task>" --repo <url>` | Delegate one CODE task, bring the diff back |
| `ask "<task>"` | Delegate a NON-code task, get the text back (no repo mounted) |
| `status [task_id]` | List tasks, or show one (works offline, no key needed) |
| `continue <task_id>` | Resume a task that reached its budget |
| `cancel <task_id>` | Stop tracking a task locally |

## Models

Only `gemini-4-argon` and `gemini-3.8-flash` are accepted. A lower model is
rejected as a usage error instead of being silently downgraded: cost and
quality are your decision.

## Limits worth knowing

- A `repository` source is capped at **500 MB** by the service. Larger
  projects need the upload path (not implemented yet).
- Sandboxes are recycled: idle for 15 minutes they are snapshotted and
  stopped, and after **7 days** they are deleted. Finish a task in the same
  session you started it.
- Free-tier quota is per project and per day. `tokens:` after each run tells
  you what it cost; `--budget` caps a single task.
- Hitting the budget is not a failure: the run exits 0, reports
  `incomplete`, and prints the exact `continue` command.

## Security

- The API key travels only in the `x-goog-api-key` header — never in a URL,
  a body, a log line or an error message.
- The remote worker never receives GitHub credentials. The repository is
  mounted read-only from its URL; git history stays local.
- Downloaded workspaces are treated as untrusted: extracted into a scratch
  directory with the safe `data` filter, stripped of the sandbox's own
  `.git` and build artifacts, and only then applied to your clone.
- Memory is scoped by the orchestrator: a worker-supplied `project` argument
  is discarded, and admin operations (delete, prune, merge) are unreachable.
- The worker may call `memory_search`, `memory_save` and `memory_context`.
  Nothing that writes to your filesystem beyond the repository, and nothing
  that deletes memory.

## Development

```bash
python3 -m unittest discover -s tests     # the whole suite, no network
python3 -m unittest tests.test_cli        # one module
```

The suite runs against a local fake of the Interactions API, so it needs no
key and no network. Real-service smoke runs are recorded in
`plan-remote-worker/03-plan-paso-a-paso.md` per work unit.

## Documentation

| Path | Contents |
|---|---|
| `plan-remote-worker/` | Product plan: PoC evidence, architecture, roadmap |
| `poc/` | Disposable scripts that proved the design, with JSON logs |
| `investigacion-*.md` | Base research document (Spanish) |
| `AGENTS.md` | Norms for every agent and human working here |

## Status

Version: see `VERSION`. Stability: **unstable** — `0.x` may break freely.