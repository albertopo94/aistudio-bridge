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

Three commands, no `.env`, no shell exports:

```bash
python3 -m pip install -e .
remote-worker setup                    # asks for your Gemini API key once (hidden input)
remote-worker run "Add a function divide(a, b) to main.py that raises
ValueError on zero, with two tests. Run the test suite." \
  --repo https://github.com/albertopo94/worker-poc-toy
```

`setup` stores the key, the default model and the budget in a private file
(`~/.remote-worker/config.json`, 0600). Re-running `setup` without a key
keeps the stored one. The environment variable `GEMINI_API_KEY` still wins
if you prefer it, but nothing requires it.

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
| `ask "<task>" --stream` | Same, but print the worker's text live as it is generated |
| `status [task_id]` | List tasks, or show one (works offline, no key needed) |
| `continue <task_id>` | Resume a task that reached its budget |
| `cancel <task_id>` | Stop tracking a task locally |

## Models

Only `gemini-4-argon` and `gemini-3.8-flash` are accepted. A lower model is
rejected as a usage error instead of being silently downgraded: cost and
quality are your decision.

Switching between them:

```bash
# change the default for all future tasks
remote-worker setup --model gemini-4-argon --budget 250000
remote-worker setup --model gemini-3.8-flash --budget 150000

# or override for a single task, leaving the default untouched
remote-worker ask "quick summary" --model gemini-3.8-flash
remote-worker run "..." --repo <url> --model gemini-4-argon
```

`gemini-3.8-flash` is the agent's native model and uses fewer tokens per
task; `gemini-4-argon` reasons deeper and spends more. Both run on the free
tier.

## run vs ask

| | `run` | `ask` |
|---|---|---|
| Task kind | code that edits a repository | everything else (research, analysis, generation) |
| Requires | `--repo <url>` | nothing |
| Returns | a diff on a local branch + test result | the worker's text |
| Sandbox files | downloaded and reviewed | not downloaded |
| Use when | the result must be reviewable and merged | you only need the answer |

Rule of thumb from the skill: if the worker must **edit code in a repo**, use
`run`; if it only needs to **answer**, use `ask`.

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
- The request trace (`REMOTE_WORKER_TRACE`) is written only when you ask for
  it, and masks credential-shaped fields before writing.
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

### Recording what was actually sent

Set `REMOTE_WORKER_TRACE` to a path and every request is appended to it as one
JSON line (`method`, `url`, `body`). Use it to answer "did the worker contract
really travel on that call?" with evidence instead of inference:

```bash
REMOTE_WORKER_TRACE=/tmp/rw-trace.jsonl remote-worker ask "..."
grep -c system_instruction /tmp/rw-trace.jsonl   # one line per turn that sent it
```

A credential is never recorded: the API key lives in a header the trace does
not read, and any `token`/`key`/`secret`-shaped field inside a body is written
as `[masked]`. Tracing is opt-in, never rewrites the request, and a trace that
cannot be written is skipped rather than failing the task.

## Documentation

| Path | Contents |
|---|---|
| `plan-remote-worker/` | Product plan: PoC evidence, architecture, roadmap |
| `poc/` | Disposable scripts that proved the design, with JSON logs |
| `investigacion-*.md` | Base research document (Spanish) |
| `AGENTS.md` | Norms for every agent and human working here |

## Status

Version: see `VERSION`. Stability: **unstable** — `0.x` may break freely.