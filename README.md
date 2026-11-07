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
| `run "<task>" --local-repo <path>` | Upload a local working tree instead of cloning a URL (see routes below) |
| `run "<task>" --repo <url> --agents-file ./AGENTS.md` | Send your own AGENTS.md where the service reads it (`/.agents/AGENTS.md`) |
| `run "<task>" --repo <url> --mount-file <local>:<target>` | Place a local text file inside the sandbox (repeatable) — skills, extra norms |
| `run "<task>" --repo <url> --image shot.png` | Send an image with the task: "look at this screenshot" (repeatable) |
| `ask "<task>"` | Delegate a NON-code task, get the text back (no repo mounted) |
| `ask "<task>" --stream` | Same, but print the worker's text live as it is generated |
| `ask "<task>" --background` | Same, but poll instead of holding one connection open (cannot combine with `--stream`) |
| `ask "<task>" --image shot.png` | Same, with an image attached (repeatable) |
| `status [task_id]` | List tasks, or show one (works offline, no key needed) |
| `status <task_id> --live` | Also ask the service for the task's current status (needs a key) |
| `continue <task_id>` | Resume a task that reached its budget |
| `cancel <task_id>` | Stop tracking a task locally, and ask the service to stop it too |
| `clean` | List the sandboxes your account still holds (deletes nothing) |
| `clean --yes` | Delete the ones no unfinished task is using |
| `routes` | Print every way code can reach the sandbox and come back from it, with measured limits (offline, no key) |
| `fetch <task> --list` | List what the task left in its sandbox, without downloading anything |
| `fetch <task> --path FILE` | Bring back that one file (`--out` to choose where, `--overwrite` to replace a local one) |
| `probe` | Upload 1 MiB to a scratch sandbox and report whether your account can upload right now (~1 s, no task quota) |

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

### What each mode can reach

The service treats the `tools` field as a **replacement** for its defaults, not
an addition — so the tool list a task sends *is* what that task can do, and the
two modes genuinely differ:

| Tool | `run` | `ask` |
|---|---|---|
| `code_execution` | yes | yes |
| `google_search` | **no** | yes |
| `url_context` | **no** | yes |
| your memory tools | yes | yes |

A code worker cannot browse the web. That is deliberate: a task scoped to one
repository has no reason to fetch arbitrary URLs, and the fewer ways it can
reach out, the less a persuasive README in that repository can do. If a task
needs both, send the research as `ask` first and hand the findings to `run`.

### Images

`--image` (repeatable) attaches a png, jpeg, gif or webp to the task, and the
agent sees it. The type is read from the file's bytes, not its name, so a
mislabelled file is sent as what it is. Audio, video and documents are refused:
this agent reads text and image files, and sending it a PDF buys a confusing
failure. `--image` travels with the turn, not into the sandbox — the file is
not left on disk there.

## What travels into the sandbox

Three channels, and which one you use changes what the agent reads by default:

| Channel | What the service does with it |
|---|---|
| `--repo` | Clones the repository at `/workspace/<name>` |
| `--local-repo` | Uploads a working tree to `/workspace/<name>` (see below) |
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

### A repository that only exists on your machine

`--repo <url>` makes the sandbox clone the repository itself, which needs a URL
it can reach and caps at 500 MB. `--local-repo <path>` covers both limits: the
tool archives your working tree and uploads it into the sandbox, so the agent
works on a repository that was never pushed anywhere.

```bash
remote-worker run "fix the flaky test" --local-repo ~/code/private-thing
```

What travels is what `git status` shows — tracked files plus untracked files
that are not ignored. Ignored build output and `.git` stay home, so the sandbox
gets a snapshot of your files without any history. The result still comes back
as a branch in your local repository, cloned from the path you named.

Two numbers worth knowing, both measured rather than guessed:

- The sandbox caps a `repository` source at **500 MB**, which is why the upload
  path exists.
- A direct upload is limited by your account's **fair-use window over a time
  span**, not by a size of the file. Measured on the same account: 1530 MiB
  accepted on fresh quota; 300 MiB refused at 290 MiB hours later; 20 MiB
  accepted minutes after that.

So the tool no longer refuses a large tree on its own: the old local guard at
1.5 GiB was a number that happened to be true on the day it was measured and
false the same evening. The upload is attempted, and when the service answers
400 "File size exceeds maximum allowed limit" the tool says what that is — the
window, not your repository — and which routes remain. The half-used sandbox
from the failed attempt is released instead of sitting in your account for its
7-day life.

### Which route gets your code there

`remote-worker routes` prints this table, and it is the same text the tool
quotes in its own error messages, so the two cannot disagree:

```
--repo <url>
    ceiling: 500 MB (documented)
    speed: cloned by the sandbox itself: no bytes leave your machine
    needs: the repository on a git host the sandbox can reach
    status: works

--local-repo <path>
    ceiling: the account's fair use window at that moment
    speed: ~1.1-1.6 MiB/s imposed by the service: 500 MB is ~6 min
    needs: nothing but the API key
    status: works

--mount-file LOCAL:TARGET
    ceiling: 1 MB per file, 2 MB in total (documented)
    speed: seconds, it rides along with the request
    needs: text files only, listed one by one
    status: works

the archive split into parts, reassembled in the sandbox
    ceiling: the same fair use window: splitting does not raise it
    speed: the same per-byte rate, plus one turn to reassemble
    needs: an extra sandbox turn to concatenate and unpack the parts; Google does not document this route, so it is unproven
    status: not validated live

a Cloud Storage bucket as the environment source
    ceiling: 2 GB (documented)
    speed: ~5 min for 1.5 GiB on a 51 Mbps uplink
    needs: your own GCP project, bucket and gsutil; the transport differs, so it is the route that escapes the upload window
    status: not implemented here
```

The last two rows are listed because they are real options to weigh, not
because this tool can run them: `shards` has never been validated against the
service and `gcs-source` needs a bucket of your own. Neither is offered as an
alternative in an error message.

Since the window is only observable by attempting an upload, `remote-worker
probe` attempts a small one: 1 MiB into a scratch sandbox, which it deletes on
every exit path, and no interaction is created — it costs bytes, not task
quota.

Sample output:

```bash
$ remote-worker probe
upload: ok -- 1.0 MiB in 1.2s at 0.8 MiB/s
the scratch sandbox was deleted again
```

Exit `0` means uploads work right now. Exit `4` means the window is spent and
says so, which is the same code a rate limit gets because it is the same kind
of answer: wait, there is nothing to fix. That is the number an agent can
branch on when it offers you a route.

The service, not your connection, sets the pace: measured 1.1-1.6 MiB/s against
a machine with a 51.86 Mbps uplink. Splitting an archive into parts does not
raise the window, it would only make a long upload resumable — which is why it
is not built yet.

Because that pace makes a large tree a multi-minute wait, `--local-repo` prints
what the service has accepted so far, the rate measured from the upload itself,
and the ETA that rate implies — on stderr, so `> report.txt` still captures
only the report. Sample output:

```
upload 5242880/314572800 bytes at 1.4 MiB/s eta 3m31s
upload 314572800/314572800 bytes at 1.5 MiB/s done in 3m22s
```

### How the work comes back

The way out has fewer routes than the way in, and that is not an oversight: a
sandbox is a filesystem with no memory of how it was filled, so there is one
transport out (the Environments files endpoint) and the real choice is
**granularity**. `remote-worker routes` prints both tables; this is the second
one:

```
the whole workspace as one tar (what `run` does every time)
    ceiling: proven small; a 1 GiB tar and a 3.6 GiB workspace both failed at ~122 s (504/500), so the ceiling is under 1 GiB and nobody knows where
    speed: ~2-3 MiB/s while it works; it stops at a service deadline, not at a size the service ever named
    needs: a task that mounted a workspace, and a sandbox inside its 7-day life. When the tar fails the bytes stay in the sandbox and come back one at a time with --path
    status: works

remote-worker fetch <task> --list [--path DIR]
    ceiling: at least 62 entries in one call (measured); no ceiling was found and none is documented
    speed: metadata only: no file content moves, 1.7s for 4 entries
    needs: the environment id of a task this tool recorded
    status: works

remote-worker fetch <task> --path FILE [--out DEST]
    ceiling: 1 GiB passed; 2 GiB was refused (504 deadline_exceeded); what lies between them is not measured
    speed: ~1.8-2.9 MiB/s measured from 100 MiB to 1 GiB
    needs: the path as `--list` printed it; an existing local file needs --overwrite
    status: works

the sandbox pushing the result to your git host
    ceiling: not applicable: no bytes travel this way here
    speed: not measured, because this route is never taken
    needs: a credential with write access to your repositories inside the sandbox, which this tool never puts there: the worker only ever receives read access, and git truth stays on your machine
    status: refused by design
```

`run` takes the first row for you: every completed run downloads the workspace,
applies it to a fresh clone on a new branch and runs your tests before you look
at it. `fetch` is for the other two questions — *what did it produce?* and *just
give me that file* — which would otherwise cost the whole archive to answer:

```bash
remote-worker fetch t_1793818082 --list
remote-worker fetch t_1793818082 --path workspace/app/tests/test_checkout.py
```

A listing moves metadata only, and neither command creates an interaction:
reading bytes back is not a task, so it spends no task quota. A file lands in
the current directory unless you say `--out`, and an existing local file is
never replaced unless you say `--overwrite` — this is the one read that could
damage your tree. A 404 is told apart by cause, because the service answers the
same 404 for two different things: a path that is not there prints `no such
path` with what the sandbox does hold and exits `2`; a sandbox that really
expired (7-day TTL) says so and exits `3` — those bytes are gone, and only a
new run brings them back.

The sizes in that table were measured on 2026-11-07 against a sandbox holding
3.6 GiB — files generated **inside** the sandbox by one cheap task turn, so the
measurement spent no upload window. They are the service's behaviour, not a
clean limit the service ever published:

- **One file came back whole at 1 GiB** in 348.9 s (~2.9 MiB/s); 2 GiB was
  refused with `504 deadline_exceeded` at 121.8 s. Where between 1 and 2 GiB
  the line sits was not measured, and the table says so rather than guessing.
- **The tar — the route `run` itself uses — died at 1 GiB** with the same 504,
  and a 3.6 GiB workspace died at ~123 s with a 500. Every workspace this tool
  has ever brought back was a few hundred KB, which is why this went unnoticed:
  a `run` whose agent generates big artifacts (datasets, models, build output)
  can spend its tokens, complete its task, and then fail at the last step with
  the service's raw error while the bytes stay reachable in the sandbox.
- Both failures landed at **~122 s whatever the payload**, so it is a deadline
  in time, not a cap in bytes — which is also why you cannot extrapolate
  upward from the measured rates.

When a run's tar fails, nothing is lost: the sandbox keeps everything for its
7-day life, and `fetch --list` + `fetch --path` bring it back file by file
under the 1 GiB single-file ceiling.

The last row is a route this tool refuses, stated with its reason instead of
left as a surprise. "Can't Google just push the result to my GitHub?" is the
most natural question about the return path, and the answer is no, on purpose:
that would mean putting a credential with write access to your repositories
inside a machine you do not control, where a persuasive README in the mounted
repository is trying to be obeyed. AGENTS.md §8 fixes the rule — the worker
never receives write credentials, git truth stays on your machine — so the tool
never puts a write credential in the sandbox, the work ends as a local branch,
and you review and push it with your own credentials. What your own agent does
afterwards (`git push`, an API call to open a PR) is your setup's business: this
tool neither does it, documents it as a feature, nor suggests it.

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
- A `repository` source is capped at **500 MB** by the service. A tree that
  large, or one with no URL the sandbox can reach, goes in by upload
  (`--local-repo`).
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
- `probe` uses the same codes for the same reason: `0` uploads work now, `4` the
  upload window is spent, `1` anything else.

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