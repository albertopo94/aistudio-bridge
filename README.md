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
- A Gemini API key. The free tier works for **Antigravity** tasks:
  environment compute is not billed during the agent's preview, and no paid
  tier was needed for any task in `plan-remote-worker/01-evidencia-poc.md`.
  That claim covers Antigravity only — see [What a task costs](#what-a-task-costs).
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
  --repo https://github.com/albertopo94/aistudio-bridge
```

`setup` stores the key, the default model and the budget in a private file
(`~/.remote-worker/config.json`, 0600). Re-running `setup` without a key
keeps the stored one. The environment variable `GEMINI_API_KEY` still wins
if you prefer it, but nothing requires it.

*Sample output* — the exact branch name, token count and changed files depend
on the task and the moment you run it. This block shows the shape of a
completed run (here, against this very repository).

```
task:   t_1793818082
status: completed
tokens: 107105 of 200000
branch: worker/aistudio-bridge-1793818082
clone:  ~/.remote-worker/aistudio-bridge/work/clone
  M  README.md
  M  remote_worker/planner.py
tests:  pass
nothing was pushed; merge the branch when you are ready.
```

Then review and merge with your own git:

```bash
cd ~/.remote-worker/<project>/work/clone
git diff main
```

## Delegating from your agent

`remote-worker` is designed to be driven by an agent, not only by a person.
From a compatible agent (one that loads skills, like gentle-shell) the
`remote-worker` skill is discovered automatically and exposes the same CLI.
The two prompts below name the trigger and the mode explicitly, so the
skill knows what to run:

**Code that edits a repository → `run`**

> Use the remote worker. In the repo
> `https://github.com/albertopo94/aistudio-bridge`, implement
> `remote_worker/planner.py` to reject any model below `gemini-3.8-flash`
> with a clear usage error, and add tests that pin the new rule. Read
> `AGENTS.md` first; the suite (`python3 -m unittest discover -s tests`)
> must pass. Bring the diff back on a branch and do not push it.

**A task that only answers → `ask`**

> Use the remote worker. Without mounting a repository, research the
> official Antigravity interaction limit and answer: what is the maximum
> total token budget per interaction, and is that a per-interaction or a
> per-day figure? Cite the source. Use the free model.

The skill turns the first prompt into `remote-worker run "..." --repo <url>`
and the second into `remote-worker ask "..."`. The worker never applies or
merges the result itself — review the branch, then merge it with your own git.

## Commands

| Command | What it does |
|---|---|
| `setup` | Store the default model and budget (`0600`, previous file backed up) |
| `setup --install-skill` | Install the remote-worker skill for gentle-shell-compatible agents |
| `setup --list-skill-backups` | List recoverable versions of the skill |
| `setup --restore-skill` | Restore the skill from its most recent backup (factory reset if it was the zero state) |
| `run "<task>" --repo <url>` | Delegate one CODE task, bring the diff back |
| `run "<task>" --repo <url> --background` | Same, but the service runs it and we poll — a dropped connection cannot lose the result |
| `run "<task>" --repo <url> --reuse-env <id>` | Run in a sandbox you already have, keeping the dependencies an earlier task installed |
| `run "<task>" --repo <url> --agents-file ./AGENTS.md` | Send your own AGENTS.md where the service reads it (`/.agents/AGENTS.md`) |
| `run "<task>" --repo <url> --mount-file <local>:<target>` | Place a local text file inside the sandbox (repeatable) — skills, extra norms |
| `ask "<task>"` | Delegate a NON-code task, get the text back (no repo mounted) |
| `ask "<task>" --stream` | Same, but print the worker's text live as it is generated |
| `ask "<task>" --background` | Same, but poll instead of holding one connection open (cannot combine with `--stream`) |
| `status [task_id]` | List tasks, or show one (works offline, no key needed) |
| `status <task_id> --live` | Also ask the service for the task's current status (needs a key) |
| `continue <task_id>` | Resume a task that reached its budget |
| `cancel <task_id>` | Stop tracking a task locally, and ask the service to stop it too |
| `clean` | List the sandboxes your account still holds (deletes nothing) |
| `clean --yes` | Delete the ones no unfinished task is using |

## Models

Only `gemini-4-argon` and `gemini-3.8-flash` are accepted. A lower model is
rejected as a usage error instead of being silently downgraded: cost and
quality are your decision.

Both are valid inline with `agent_config`. Note that Google's *managed agent*
documentation lists only `gemini-3.8-flash`, `gemini-3.6-flash` and
`gemini-3.5-flash-lite` for saved agents — `gemini-4-argon` is not in that
set. The product sends `agent_config` per interaction (which is what makes
per-task model switching possible), and Argon is proven working that way, but
if Google ever narrows the inline set to the managed one, `--model
gemini-4-argon` would break. The floor is your choice, not an availability
limit: two more models exist and are deliberately refused.

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

## What travels into the sandbox

Three channels, and which one you use changes what the agent reads by default:

| Channel | What the service does with it |
|---|---|
| `--repo` | Clones the repository at `/workspace/<name>` |
| `--agents-file` | Written to `/.agents/AGENTS.md`, which the service **auto-loads as system instructions** |
| `--mount-file local:target` | Written verbatim at `target`; `.agents/skills/<name>/SKILL.md` is auto-discovered as a skill |

A repository's own `AGENTS.md` lands at `/workspace/<name>/AGENTS.md`, which is
**not** an auto-load location: the worker reads it because the task contract
orders it to, and it never reads a skill that lives only inside the repo. If
you want either to be enforced rather than instructed, send it in:

```bash
remote-worker run "..." --repo <url> \
  --agents-file ./AGENTS.md \
  --mount-file ./.agents/skills/building/SKILL.md:/.agents/skills/building/SKILL.md
```

Inline files are capped by the service at 1 MB each and 2 MB in total, and must
be text — the tool checks both before sending anything.

`--reuse-env <environment_id>` runs the task in a sandbox you already have, so
dependencies an earlier task installed are still there. `clean` prints the ids
you hold. The task's repository is still mounted into it.

## Limits worth knowing

**Two clocks expire, and confusing them costs you a task:**

| What | How long | When it goes away |
|---|---|---|
| The **sandbox** (your files) | 7 days; snapshotted after 15 min idle | `continue` cannot resume it — start a new run |
| The **interaction** (the conversation) | 1 day on the free tier, 55 on a paid tier | `previous_interaction_id` stops resolving |

A task left over the weekend is gone even where the sandbox still exists, and
on the free tier a `continue` is a day late after midnight. Finish a task in
the session you started it.

**The sandbox.** Ubuntu with `curl`, `wget`, `git`, `jq`, `ripgrep` and
`gcloud`; Python 3.12 with numpy, pandas, requests and google-genai; Node 22
with typescript and create-vite. 4 cores and 16 GB, fixed. Startup is around
5 seconds. The worker reads **text and image** files only — a repository with
binaries has parts it cannot see. A `pip install` or `npm install` the worker
runs during a task lasts only for that sandbox: it survives only if the same
environment is reused.

**Budgets.** `max_total_tokens` is best-effort: it is checked between steps,
so a run can overshoot slightly before reporting `incomplete`. Cached tokens do
not count against it. Separately, the service compacts long conversations
automatically around 135k tokens — that protects the *context*, while your
budget caps *total usage*; they are not the same number.

**Request shape.** Generation parameters (`temperature`, `top_p`, `top_k`,
`stop_sequences`, `max_output_tokens`) are rejected with a 400 by this agent,
and it cannot force a JSON schema on its answer. The product sends neither,
which is an invariant, not an oversight.

**Version pin.** The agent is addressed as `antigravity-preview-09-2026`: an
explicit preview revision. Google documents no deprecation policy for it, so
when that revision is retired the first symptom is an error naming the agent,
and the fix is one constant in `remote_worker/runner.py`.

- Sandboxes outlive their tasks. A finished task leaves its sandbox — and the
  repository mounted in it — on Google's servers for the rest of its 7-day
  life. `clean` shows what you hold; `clean --yes` deletes what no unfinished
  task is using. It lists the whole account's environments, not just the ones
  this tool created, and it never deletes one an unfinished task points at.
- A `repository` source is capped at **500 MB** by the service. Larger
  projects need the upload path (not implemented yet).
- A foreground request holds one connection for the whole task and gives up at
  600 seconds, losing the result in your terminal even though the sandbox kept
  working. For anything long, use `--background`: the service runs the task,
  and this tool polls it. When the watch runs out the task is still not lost —
  `status <task_id> --live` reads the real status, `continue` picks it up.
- Free-tier quota is per project and per day. `tokens:` after each run tells
  you what it cost; `--budget` caps a single task.
- Hitting the budget is not a failure: the run exits 0, reports
  `incomplete`, and prints the exact `continue` command.
- A rate limit exits with the API's own message and **never retries**. That is
  deliberate: a continuation carries `previous_interaction_id`, so a blind
  retry is not idempotent — it would fork a second branch of the conversation
  and bill the same work twice. Wait, then re-run the printed command. A rate
  limit exits 4; other API failures exit 1.

## What a task costs

Antigravity tasks cost **US$0** on the free tier for every task in this
project's evidence (preview, no billing account attached). For planning past
the preview, Google's own baselines are **US$0.25–3.25** for a typical task
and roughly US$5 at the high end, with 50–70% of input usually cached. Real
counts from this product: ~6k tokens for a memory lookup, ~23k trivial,
~29k–97k for a code task.

A Deep Research backend would not share that price — about US$1–3 per task
(US$3–7 for the Max variant), with no declared free-tier eligibility. That is
one reason it is not the engine behind `ask`.

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

### What Google keeps server-side

Storage is on by default and the product does not turn it off, because it
cannot: a task is resumed through `previous_interaction_id`, which is
incompatible with `store=false`. So for every task the API keeps — server
side, browsable at [aistudio.google.com/logs](https://aistudio.google.com/logs)
for 1 day on the free tier and 55 on a paid tier — the task text, the context
of the mounted repository, and whatever the memory tools returned.

Treat that as a real boundary. Do not delegate a repository whose contents
must not reach Google, and do not put secrets in a task prompt. There is no
credential in the sandbox for an injected instruction to steal, which is the
point of the design — but the task text itself is not private.

### Delegate against repositories you trust

A mounted repository can carry its own `.agents/hooks.json`, and the service
runs those hooks inside the sandbox on the agent's first tool call. So a
third-party repository can execute code in the sandbox and — while sandbox
egress is unlimited, which is the default — read `/workspace` and send it
somewhere else. Against your own repositories the risk is effectively nil;
against an unfamiliar fork it is not.

Delegate only against repositories you own or trust, or fence the egress and
make the exfiltration fail:

```bash
remote-worker run "..." --repo <url> --allow-domain pypi.org
remote-worker run "..." --repo <url> --no-network
```

The allowlist matches domains, not paths, and `*.example.com` does not cover
`example.com` — the tool warns you when you left the root out. The fence is
declared when the sandbox is created and every later turn rejoins that same
sandbox, so it holds for the whole task. A task that needs to install packages
needs the registries allowlisted, or it fails in a way the worker did not
cause.

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