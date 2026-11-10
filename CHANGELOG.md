# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) while
unstable: `0.x` may break freely.

## [0.25.5] — 2026-11-10

### Documentation

- The README, the skill text and this changelog now describe what the code
  actually does after the hardening pass: tests are not run on the host without
  `--run-tests`, memory is read-only without `--memory-save`, a delivery is one
  commit that carries deletions, a completed `continue` delivers, and task ids
  are unique.

## [0.25.4] — 2026-11-10

### Fixed

- `ask --stream` printed the answer twice (the deltas live, then the completed
  text again). It now prints once, and a streamed answer is not re-printed by
  the report.
- `ask --stream --image` silently dropped the images: the streaming branch sent
  the bare task string while the other branch sent text plus image parts. Both
  now send the same input.

## [0.25.3] — 2026-11-10

### Fixed

- A task interrupted mid-run is now recoverable. Each function round creates a
  new interaction and its id was never stored, so a process killed between
  rounds left the record naming the previous conversation: `status --live` read
  a stale id and `continue` refused the task it could have resumed. The newest
  interaction id and `in_progress` are persisted after every round, and
  `continue` accepts `in_progress` as resumable.

## [0.25.2] — 2026-11-10

### Fixed

- Task ids are unique. They were `t_<epoch seconds>`, so two launches inside one
  second shared an id and the store merged them: the second task silently took
  the first one's interaction, environment and branch, and `checkout -B`
  re-pointed the shared branch. The id now carries a random suffix and the
  branch is derived from it; a write that would fuse two different tasks is
  refused instead of overwriting a paid-for record.

## [0.25.1] — 2026-11-10

### Fixed

- A continuation that completes now delivers its code. `run` downloaded the
  workspace, applied it to a branch and recorded the result; `continue_task`
  resumed the interaction, wrote the status and returned — so a task that hit
  its budget, continued and then completed in Google was paid for and never
  came home. Both paths share one delivery operation, including the
  `--run-tests` policy and the `undelivered` outcome when the bytes cannot be
  brought back.

## [0.25.0] — 2026-11-10

### Changed

- **Behaviour change:** `run` no longer executes the delivered project's tests
  on your machine. The code arrives from a remote agent, and running its tests
  means running its code with your environment. Pass `--run-tests` to opt in;
  the report now prints `tests: not run (pass --run-tests …)` instead of
  nothing, so a clean exit is never mistaken for a green suite.

### Added

- `--run-tests` (for `run` and `continue`). On that path the test child gets a
  scrubbed environment built by allowlist (PATH, locale, TMPDIR, …) with `HOME`
  pointed at the scratch copy, so API keys, cloud credentials, the SSH agent
  and `~/.ssh` are out of reach. `sync.run_tests()` scrubs for every caller.

## [0.24.3] — 2026-11-10

### Changed

- **Behaviour change:** the remote worker no longer writes to your memory. It
  could call `memory_save` by default and put a sentence it invented — or text
  lifted from a poisoned repository — into the durable store that steers later
  sessions. Memory is read-only unless you pass `--memory-save` (on `run`,
  `ask` or `continue`), and the tool is not even advertised without it.

### Added

- Bounds on an opted-in write: title 200 characters, content 8000, a plain
  category word; empty, oversized or malformed input is refused and reported to
  the worker rather than silently stored.

## [0.24.2] — 2026-11-10

### Fixed

- Each delivery is a clean, committed unit. `apply_to_branch` staged onto
  whatever the previous task left in the persistent clone and never committed,
  so a new task's diff reported the previous task's work.
- A file the remote agent deleted now reaches the branch: the overlay only ever
  copied, so deletions silently never landed. Removals are applied before the
  overlay, against tracked files only, so your own untracked files in the clone
  survive and stay out of the delivery commit.

## [0.24.1] — 2026-11-10

### Security

- Workspace extraction is fail-closed on every supported Python. The fallback
  `extractall(dest)` with no filter ran precisely on unpatched 3.10/3.11 — the
  versions the declared `>=3.10` floor still supports, where no tarfile path
  traversal fix exists. Every header is now validated before a byte is written:
  absolute and escaping paths, symlink/hardlink/device members and a 4 GiB
  declared total abort the delivery (`undelivered`, sandbox kept), and an older
  interpreter extracts pre-validated regular members through a size-capped loop
  instead of an unfiltered extract.

## [0.24.0] — 2026-11-07

### Added

- `run --gcs-source gs://bucket/prefix`: a fourth source route. The sandbox
  copies the tree from a Cloud Storage bucket at mount time (documented
  2 GB per source), so the upload is the user's gcloud job and never
  touches the fair-use upload window. Private buckets work through the
  service's egress proxy: the product resolves a bearer token
  (`--gcs-token-file`, else `gcloud auth print-access-token`) and injects
  it for `storage.googleapis.com` on the allowlist; the token stays in
  memory, is never printed or recorded, and a public bucket needs no
  credential at all. A user `--allow-domain` fence keeps its shape (the
  GCS rule is added, no wildcard); a token with `--no-network` is a
  usage error. The gcs-source row becomes a built route but stays
  "not validated live" until a live run says otherwise (S11), and the
  README carries a GCP-novice setup checklist (project, bucket, gcloud
  auth, upload, run).

## [0.23.1] — 2026-11-07

### Changed

- The sharded-upload route is no longer advertised as unproven: the PoC
  (poc/t24_shards.py) ran it live against the real service and the tree
  came back byte-identical (L48). The routes table and the README now
  say "proven live in the PoC, not implemented here" with the run's
  numbers, and the route stays out of the alternatives an error
  message offers.

## [0.23.0] — 2026-11-07

### Added

- `run --delete-env`: delete the sandbox once the delivery is verified.
  A failed delivery (`undelivered`) always keeps it, because the sandbox is
  then the only copy of the work. Without the flag, a delivered run names
  the sandbox and asks -- a `[y/N]` prompt in a terminal (keep is the
  default), or the same question plus the `clean` command when an agent
  drives the CLI. A delete that fails is reported; it never turns a
  delivered run into a failure.

## [0.22.5] — 2026-11-07

### Fixed

- Two defects found by the first live runs of the local-repo path (T25):
  `run --local-repo` referenced its sandbox with an invented `{"id": ...}`
  object, which the service rejects ("Provide a 'type' field (for
  EnvironmentConfig) at 'environment'"); it now passes the environment id
  the documented way, and `--reuse-env` uses
  `{type: remote, environment_id, sources}` so the sandbox is updated
  instead of created. The test fake now mirrors that 400, so no invented
  shape can go green again.
- A delivery can no longer land on a clone of another repository.
  `apply_to_branch` reused whatever clone existed at the per-project clone
  path, so the branch of a task for repository B could be applied to the
  history of repository A (found live: the branch went into the
  worker-poc-toy clone from the PoC era, and the real repository never
  received it). The clone is now used only when its origin is that exact
  repository; a mismatched clone is kept under a dated backup name and the
  right one is cloned.

## [0.22.4] — 2026-11-07

### Changed

- Docs only: ADR 0001 (aistudio-bridge as a standalone product, not part
  of agy-model-bridge) now has a draft in `docs/adr/` for the user's
  sign-off, and the plan lines pointing at it are updated.

## [0.22.3] — 2026-11-07

### Fixed

- `clean` no longer offers the sandbox of an unfinished task in another
  project for deletion. The service's environment list is account-wide
  (the Environments API has no per-project scope), but the protection set
  only read the project the CLI was run from, so `clean --yes` from one
  project could delete a live sandbox owned by an active task in another.
  The protection now spans every local project store under the state
  root, so unfinished work is never offered for deletion no matter which
  project the CLI runs from (F2).

## [0.22.2] — 2026-11-07

### Changed

- Docs only: the T11/G23 disposition is recorded (a skill reaching the
  sandbox works via `--mount-file LOCAL:TARGET`, so no dedicated `--skill`
  flag), the session decisions for the remaining items are logged in the
  feature doc (GCS route approved to build; live quota OK for T24/T25),
  and the plan's open-items line no longer presents the download ceiling as
  unmeasured (T27 measured it in v0.22.0). No code touched.

## [0.22.1] — 2026-11-07

### Fixed

- A task whose agent finished but whose workspace could not come back is no
  longer recorded `completed`. The return path (download + apply) is now one
  unit with a stated outcome: if it fails — service 500/504, a timeout, an
  unreadable tar, or a git failure — the task records `undelivered`. That
  status is deliberately not in the `clean` "finished" set, because for this
  task the sandbox is the only copy of work the agent already paid for, and
  `clean --yes` would have deleted it as if it were done. `continue` refuses
  it (the turn is over); the CLI prints the recovery — `fetch <task> --list`,
  `fetch <task> --path <file>` — the 7-day TTL — and exits 3. A partial
  archive left by a failed download is removed. Found by the T27 measurement:
  a 3.6 GiB workspace answered HTTP 500 after the agent had spent 93k tokens
  (odd L39); before this fix the task said `completed` and the branch never
  appeared.
## [0.22.0] — 2026-11-07

### Changed

- The return-path table now carries measured numbers instead of three
  `not measured` cells (T27, user-authorized live run; files generated inside
  the sandbox so no upload window was spent). Single file: 1 GiB passed in
  348.9 s (~2.9 MiB/s), 2 GiB refused `504 deadline_exceeded` at 121.8 s; what
  lies between stays explicitly unmeasured. The recursive tar — the transport
  every `run` uses — refused 1 GiB with 504 and a 3.6 GiB workspace with 500,
  both at ~122 s: the limit is a service deadline in time, not a documented
  size. README and the skill carry the same rendered table (drift-pinned).

### Known follow-up recorded

- F1: when that tar fails, the task is already recorded `completed` and the
  raw service error escapes while no branch is created. Recorded in
  `odd/tasks/remote-worker-gaps.md` with its evidence; not fixed here.

## [0.21.2] — 2026-11-07

### Fixed

- `fetch <task> --list` printed paths with no sizes. The listing reader asked the
  response for `sizeBytes`; the Environments files endpoint answers with
  `size_bytes`. Verified live against 12 sandboxes still held by the account:
  every entry carries `name`, `path`, `type`, `created`, `modified`, and files
  additionally `mime_type` and `size_bytes`, while a `DIRECTORY` entry carries no
  size at all. The reader now takes `size_bytes` first, and a directory prints no
  size rather than borrowing one.
- The test fixture for the listing had invented the same wrong field name, so the
  rule was green while the command was broken. The fixture now mirrors the shape
  the service returns, which is the only reason this was visible as a RED.
- `fetch` no longer reports a wrong path as an expired sandbox. The files
  endpoint answers the identical 404 body for "this path is not here" and "this
  sandbox expired", and the old reader assumed the second: it told users to start
  a new run (quota, and a branch, for nothing) while hiding the real fix. The two
  are told apart by a root listing, which is metadata only, and the exit codes
  separate (2 wrong path, 3 expired). When the root cannot be read the tool
  declines to guess.

### Documentation

- Listing a path the sandbox does not have answers 404 with a readable message
  (`Path 'works…' does not exist`), and listing an environment's root answers an
  empty list rather than an error; both observed live, no code change needed.

## [0.21.1] — 2026-11-07

### Documentation

- The plan's gap index stopped describing intentions and started describing the
  repository. It had sorted G1–G30 by the priority each gap had on 2026-11-05, so
  nine closed gaps still read as pending work; it is now grouped by *how* each one
  closed (code / documentation / closed by live evidence / dropped), and the rows
  that stayed say what is genuinely open — the unmeasured download ceiling,
  unvalidated shards, the live `--local-repo` run and the `--delete-env` decision.
- Two claims in the closing report contradicted the code and are corrected: `run
  --local-repo` was credited with a guard against the measured upload ceiling, which
  was deleted in 0.18.0 because a fixed number cannot describe a time-window limit;
  and the upload ceiling was stated as a fact ("real ceiling 1.5 GiB"), which is the
  number the fair-use measurement retracted the same evening it was taken.
- No behavior changed: no source, test or CLI file is touched by this release.

## [0.21.0] — 2026-11-07

### Added

- `fetch` reads what a task left in its sandbox without taking the whole
  workspace back: `fetch <task> --list` moves metadata only, and
  `fetch <task> --path FILE` brings that one file (`--out` to choose where, and
  an existing local file is never replaced unless you say `--overwrite`). Both
  granularities already existed in the client and nothing could reach them; both
  create no interaction, because reading bytes back is not a task.
- The way back out of the sandbox is now a table like the way in, printed by the
  same `routes` command: one transport, three granularities, and the git-host
  push listed as `refused by design` with its reason (a write credential would
  have to live inside a sandbox you do not control). README and the skill embed
  the rendered table, so no copy can drift from the CLI's.

### Documentation

- Three return-path ceilings say **not measured** instead of guessing. No
  download ceiling is documented or observed, and measuring it honestly needs a
  large tree inside a sandbox first, which costs upload window or task quota. A
  number there would be the same failure class as the deleted 1.5 GiB upload
  constant.
- README says why the worker never pushes to your git host, and no longer claims
  the upload path is "not implemented yet".

## [0.20.1] — 2026-11-06

### Documentation

- The feature document (`odd/tasks/remote-worker-gaps.md`) records the commit
  each task landed in, so the log can be followed from a task to the code.

## [0.20.0] — 2026-11-06

### Added

- `probe` answers whether the account can upload right now: 1 MiB into a
  scratch sandbox, which is deleted on every exit path including a refusal, and
  no interaction is created, so the answer costs bytes and not task quota. Exit
  `0` uploads work, exit `4` the fair-use window is spent — the same code a
  rate limit gets, because it is the same kind of answer — exit `1` anything
  else. The window cannot be read from anywhere, only attempted, and this is
  the cheap way to attempt it: a fact from one minute ago instead of an
  average, which is what choosing a route actually needs.
- When a sandbox survives a failed upload or probe, the message says so and
  names `clean --yes`, instead of letting it hold the account unnoticed.

## [0.19.0] — 2026-11-06

### Added

- `run --local-repo` reports the upload while it runs: bytes the service has
  accepted, bytes total, the rate measured from this upload and the ETA that
  rate implies, then a closing line with the total time. At the ~1.1-1.6 MiB/s
  the service imposes, 500 MB is about six minutes and 1.5 GiB about sixteen,
  and silence that long is indistinguishable from a hang. Progress goes to
  stderr, so `> report.txt` still captures only the report, and bytes the
  service has not acknowledged are never claimed as sent.

## [0.18.0] — 2026-11-06

### Added

- `routes` prints every way code can reach the sandbox — `--repo`,
  `--local-repo`, `--mount-file`, sharded upload, a Cloud Storage source — each
  with its ceiling, its measured speed, what it needs and whether it works
  here. It is offline and needs no key, so it can be read before spending any
  quota. The same rendered table is embedded in the skill and in this README,
  and a test fails if either drifts: the CLI's error messages, the agent's
  menu and the docs cannot disagree about the limits (G4/G5 follow-up, S11).
- A refused upload is now explained instead of reported. The service answers
  400 "File size exceeds maximum allowed limit" when the account's fair-use
  window for uploads is spent over a time span, which reads like a property of
  the file and is not; the message now says it is the window, that retrying
  later is the fix, and which routes could carry the bytes another way.
- A sandbox created by an upload attempt that then failed is deleted before
  the error is raised, so a half-used sandbox does not hold the account for
  its 7-day life. When the service refuses that deletion the message says so
  and names `clean --yes`.

### Changed

- **The local 1.5 GiB upload ceiling is gone.** `run --local-repo` no longer
  refuses a tree for its size. The number was measured once, on fresh quota;
  the same account later refused 300 MiB at 290 MiB and accepted 20 MiB minutes
  after, so no local constant can know the limit and the old guard stopped
  work that the service would have accepted. The service is asked and its
  refusal is translated. `sync.UPLOAD_LIMIT_BYTES` was removed with it.

## [0.17.0] — 2026-11-06

### Added

- `run --local-repo <path>` delegates a task against a repository that never
  leaves the machine. The working tree is archived (tracked files plus
  untracked-but-not-ignored ones, never `.git`), uploaded into a fresh sandbox
  with a resumable session and unpacked at `/workspace/<name>`, and the result
  comes back as a branch cloned from that local path. It is the only route for
  a local-only repository, and the one for a tree over the 500 MB
  `repository` source limit, up to the measured 1.5 GiB upload ceiling (G4).
- `UploadClient.upload_file` streams a file chunk by chunk, so a large archive
  never has to fit in memory.
- `Sync.archive_repo` builds that archive to a file instead of in memory, for
  the same reason.

### Fixed

- An upload target ending in `/` keeps its trailing slash. `_quote_path`
  stripped it, which would have made the service unpack an archive under a
  directory named after the file instead of into the target directory.

## [0.17.0] — 2026-11-06

### Added

- `run --local-repo <path>` delegates a task against a repository that never
  leaves the machine. The working tree is archived (tracked files plus
  untracked-but-not-ignored ones, never `.git`), uploaded into a fresh sandbox
  with a resumable session and unpacked at `/workspace/<name>`, and the result
  comes back as a branch cloned from that local path. It is the only route for
  a local-only repository, and the one for a tree over the 500 MB `repository`
  source limit, up to the measured 1.5 GiB upload ceiling (G4).
- `UploadClient.upload_file` streams a file chunk by chunk, so a large archive
  never has to fit in memory.
- `archive_repo` builds that archive on disk instead of in memory, for the
  same reason.

### Fixed

- An upload target ending in `/` keeps its trailing slash. It was being
  stripped, which would have made the service unpack an archive under a
  directory named after the file instead of into the target directory.

## [0.16.0] — 2026-11-06

### Added

- `UploadClient` (`remote_worker/google_client.py`): a resumable upload to an
  environment. The session is opened with `PUT /upload/v1beta/environments/
  {env}/files/{path}?uploadType=resumable`, then the file goes out in 10 MB
  chunks with a `Content-Range` header. A `308 Resume Incomplete` moves the
  cursor to where the server says it committed; a `308` without a usable
  `Range` raises `UploadResumeError` instead of resuming from a guessed
  offset. A non-308 reply fails the upload with the status and the body.
- `tar_gz_bytes` (`remote_worker/sync.py`): packs `{path: bytes}` into a
  gzip tarball in memory, so a repository over the 500 MB `repository`
  source cap can travel as an uploaded archive (G4 groundwork; the PoC
  `poc/g5_upload_ceiling.sh` measures the direct-upload ceiling that decides
  whether the GCS fallback is needed).

## [0.15.0] — 2026-11-06

### Added

- `run --image <path>` and `ask --image <path>` (repeatable) attach a png,
  jpeg, gif or webp to the task. The mime type is read from the bytes, not
  the filename. Audio, video and documents are refused before anything is
  sent (G11, G28).

### Changed

- README documents that `tools` replaces the service defaults (not adds to
  them), with the exact table of what `run` and `ask` each carry (G24).

## [0.14.0] — 2026-11-06

### Added

- `run --reuse-env <environment_id>` runs the task in a sandbox you already
  hold, so dependencies an earlier task installed are still there. The
  repository is still mounted into it (G7).
- `run --agents-file <path>` sends a local AGENTS.md to `/.agents/AGENTS.md`,
  the location the service auto-loads as system instructions. A repository's
  own AGENTS.md lands at `/workspace/<name>/AGENTS.md`, which is not such a
  place, so until now it was read only because the worker contract asks the
  agent to (G22).
- `run --mount-file <local>:<target>` (repeatable) places any local text file
  inside the sandbox, which is how `.agents/skills/<name>/SKILL.md` — the
  documented skill channel — finally reaches the agent (G23). Inline files are
  checked against the service's ceilings (1 MB each, 2 MB total, text only)
  before anything is sent.

## [0.13.0] — 2026-11-06

### Added

- `run --allow-domain <domain>` (repeatable) and `run --no-network`: limit or
  close the sandbox's outbound traffic instead of accepting the default
  anything-goes egress. The fence is set when the sandbox is created, and
  every continuation rejoins it by id. The tool warns when a `*.example.com`
  entry lacks its bare `example.com`, which the service's wildcard does not
  cover (G1).
- `clean` lists the sandboxes your account still holds; `clean --yes` deletes
  the ones no unfinished task is using. Finished tasks used to leave their
  sandbox — mounted repository included — alive for the full 7-day TTL (G2).

### Changed

- README "Delegate against repositories you trust" now names the flag that
  makes exfiltration from a third-party repository fail, instead of asking
  for trust alone.

## [0.12.0] — 2026-11-06

### Added

- `run` and `ask` accept `--background`: the service runs the task and this
  tool polls the stored interaction until it finishes, so a dropped connection
  no longer loses a long task (G13). The watch is bounded (5 s apart, 360
  reads by default); when it ends the task stays resumable and the report
  names the command that picks it up. `--stream` and `--background` together
  are a usage error on purpose.
- `continue` now checks the interaction first and refuses to race one the
  service still reports as running (chaining to it is a documented 400), and a
  conversation past its retention is reported expired at the check instead of
  at the continuation attempt.

### Fixed

- `continue` on a still-running task no longer prints "the agent reached its
  token budget" — false, and it advertised the exact command the tool had
  just refused. Incomplete results carry an explicit reason now.

## [0.11.0] — 2026-11-06

### Added

- `status <task_id> --live` asks the service for the interaction's current
  status. `status` without the flag still sends no request and needs no API
  key. Reading an interaction is the only way to learn what became of work this
  process stopped watching, because the create response carries only the
  model's own steps; a record past its retention is reported as stale (G18).
- `cancel` also stops the interaction on the service
  (`POST /v1beta/interactions/{id}/cancel`, no body) instead of only writing
  local state. The local cancel always happens; the remote one is best-effort,
  because the service documents cancellation only for running interactions and
  says nothing about one that already finished, so a refusal is reported rather
  than turned into a failed command (G12).

### Changed

- `cancel` now says whether the service was reached. It previously printed
  "local tracking only" even when the remote stop had succeeded.
- An API error no longer escapes as a traceback. `main` handled only `KeyError`
  and `ValueError`, so a rate limit printed a Python stack. A 429 now exits 4
  and explains that the quota window is what to wait on, and why the client
  never retries automatically: a continuation carries
  `previous_interaction_id`, so a blind retry is not idempotent — it forks the
  conversation and bills the same work twice. Other API errors keep their own
  message and exit 1 (G19).

## [0.10.1] — 2026-11-06

### Fixed

- `continue` against an expired sandbox reports it plainly instead of the raw
  API error. A 404 while resuming now returns status `expired` with a message
  naming the 7-day sandbox TTL, echoing the task's own token budget when the
  record has one, and stating that the task cannot be resumed. The task is
  marked `expired` in state, so `status` no longer offers it as resumable and
  the CLI no longer prints a `continue` command that could never work. A
  non-404 API error still propagates, because a server hiccup is not an
  expiry, and the task keeps its resumable status. One attempt: a 404 is
  terminal, so nothing is retried (G9, with the clock distinction from G16).

## [0.10.0] — 2026-11-06

### Added

- `REMOTE_WORKER_TRACE`: point it at a path and every request is appended as
  one JSON line (method, URL, body). The API key is never recorded — it lives
  in a header the trace does not read — and credential-shaped fields inside a
  body are written as `[masked]`. Tracing is opt-in, never alters the request
  that goes out, and an unwritable path is skipped rather than failing the
  task. It exists so "did the worker contract really travel on that call?"
  is answered with evidence: the first live run proved a continuation carries
  the same 1052-character contract and the same tool set as the first turn.
- `-v` as an alias of `--version`.
- README: a "Delegating from your agent" section with example prompts that
  name the trigger and the mode (`run` for code, `ask` for answers).

### Changed

- The "First run" example points at this repository instead of the PoC toy
  repository, and its output block is labelled as sample output.
- "Limits worth known" separates the two clocks that expire — the sandbox at
  7 days and the interaction at 1 day on the free tier — which the previous
  wording conflated, and documents the sandbox stack, what the worker can
  read (text and image only), that `max_total_tokens` is best-effort, that
  generation parameters are rejected, and why a rate limit never retries.
- "Security" now states what stays server-side at Google, why `store=false`
  is not available to a resumable task, and why to delegate only against
  repositories you own or trust (a mounted repo can ship `.agents/hooks.json`).
- New "What a task costs" section; the free-tier claim is scoped to
  Antigravity, since a Deep Research backend costs US$1–7 per task.
- `gemini-4-argon` is documented as outside Google's managed-agent model set:
  it works inline today, and `--model gemini-4-argon` is what would break if
  that set ever narrowed.

## [0.9.3] — 2026-11-05

### Fixed

- Every continuation now re-sends the worker contract and the tool set
  (G15/G22). The Interactions API scopes `system_instruction` and
  `tools` to one interaction, so previously each memory round-trip and
  every `continue` resumed the task without the anti-push contract,
  without the read-AGENTS.md rule and without the memory tools.
  `run` records `kind` and `repo_dir` so `continue` rebuilds the exact
  contract; legacy records without `repo_dir` still continue.

## [0.9.2] — 2026-11-05

### Changed

- `.gitignore` now covers the setuptools `aistudio_bridge.egg-info/`
  directory left behind by the editable install used for the global
  `remote-worker` CLI deploy.

## [0.9.1] — 2026-11-05

### Fixed

- The `remote-worker` skill no longer tells agents to rely on the
  `GEMINI_API_KEY` environment variable (PoC era). It documents the real
  resolution order from 0.9.0: the environment variable wins, then the
  private stored config in `~/.remote-worker/config.json` written by
  `setup`. Pinned with a test assertion on the installed SKILL.md.


### Added

- `setup` stores the Gemini API key in the private config file (0600):
  prompts once with hidden input, or accepts `--api-key`. Re-running setup
  without a key keeps the stored one.
- `run`/`ask`/`continue` resolve the key in order: `GEMINI_API_KEY`
  environment, then the stored config. A new user needs **no** `.env` and no
  shell exports.

### Changed

- README rewritten for the zero-friction first run: setup asks for the key,
  the model switching section explains how to alternate between
  `gemini-4-argon` and `gemini-3.8-flash`, and a new run-vs-ask table
  explains which mode fits which task.

## [0.8.0] — 2026-11-04

### Added

- Live streaming (SSE) for `remote-worker ask --stream`: the worker's text
  is printed as it is generated instead of waiting silently for the full
  round trip. Verified against the real API. Non-streaming calls are
  unchanged.

## [0.7.0] — 2026-11-04

### Added

- `remote-worker ask "<task>"`: delegate a NON-code task (research, analysis,
  generation) and get the worker's text back. No repository is mounted, no
  workspace is downloaded; memory callbacks still resolve locally. This is
  the mode for offloading token cost from the local model to Google's free
  tier, or simply using the agent for everything it can do.
- Skill installer: `remote-worker setup --install-skill` writes a
  gentle-shell-compatible skill (delegation rubric included) into
  `~/.agents/skills/remote-worker/SKILL.md`, with timestamped backups, a
  zero-state marker for the first install, `--list-skill-backups` and
  `--restore-skill` (factory reset when restoring the zero state). The
  backup/restore/factory-reset shape mirrors agy-model-bridge.
- TODO (documented in the plan): `ask` with an optional mounted repo
  (analysis without diff), multi-agent support (codex, claude-code,
  opencode), streaming SSE, private repositories, ADR 0001.

## [0.6.0] — 2026-11-04

### Added

- `remote-worker setup` stores the default model and budget in a private
  `~/.remote-worker/config.json`, keeping a timestamped backup of any
  previous file. `run` now uses those defaults when no flag is given.
- Complete README: install, first run, command table, model floor, limits,
  security properties and development workflow.
- `tests/test_config.py`, `tests/test_cli.py` coverage for the stored
  defaults.

## [0.5.0] — 2026-11-04

### Added

- CLI (`remote-worker`) with `run`, `status`, `continue` and `cancel`.
- `remote_worker/planner.py`: model floor (`gemini-4-argon`,
  `gemini-3.8-flash`), explicit `max_total_tokens`, and the worker contract.
- `remote_worker/state.py`: private per-project task records, newest first.
- `remote_worker/runner.py`: glue from request to branch, diff and test run.

### Fixed

- A memory failure no longer aborts a task: the worker receives
  `{ok: false, error: …}` and can report that memory is unavailable.
- The clone directory lives under the state root, so a recorded task is
  resumable in a later session.
- `status` and `cancel` no longer require `GEMINI_API_KEY`.
- The default Engram project is the working directory, not the remote
  repository name.

## [0.4.0] — 2026-11-04

### Added

- `FilesClient`: list and download environment files, including a directory
  as a tar archive via the current endpoint.
- `sync.py`: `mount_repo`, `apply_to_branch`, `run_tests`.

### Fixed

- The sandbox's own `.git` and build artifacts are purged before the overlay;
  a root-owned `__pycache__` used to abort `git add`.

## [0.3.0] — 2026-11-04

### Added

- `engram_adapter.py`: local Engram callbacks with the project pinned by the
  orchestrator and an allowlist of memory tools.
- `function_loop.py`: bounded `requires_action` loop with explicit
  continuity (`previous_interaction_id`, `environment`, `agent`).

## [0.2.0] — 2026-11-04

### Added

- `google_client.py`: standard-library client for `POST /v1beta/interactions`,
  with `last_model_output` and `usage` helpers.

## [0.1.0] — 2026-11-04

### Added

- Repository base: working norms (`AGENTS.md`), product plan
  (`plan-remote-worker/`), and the proof-of-concept evidence with its logs
  (`poc/`).