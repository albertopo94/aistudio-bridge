# Feature: close the G1–G30 gap backlog of remote-worker

Delegated-task CLI (`remote-worker`, repo `aistudio-bridge`). After 7 documentation
audits the plan lists 30 gaps (G1–G30) plus pending items (Q1–Q3 self-test, streaming for
`run`, ADR 0001). G15 was fixed in v0.9.3 and still needs live proof. This feature closes
the whole backlog, work unit by work unit, on a feature branch.

## Specs

- **S1** (user, verbatim): "sigue el orden que mejor consideres, me da igual siempre y
  cuando lo solventemos/implementemos todo." → the orchestrator picks the order; nothing is
  left open without an explicit user disposition.
- **S2** (user, verbatim): "recordaba que habian mas G listados en "TODO" por hacer, me
  equivoque?" → the backlog is the consolidated index G1–G30 of
  `plan-remote-worker/README.md`, §"Índice de gaps", plus the two `### TODO` sections with
  design (G5 PoC, G9 friendly 404).
- **S3** Q1: a "Delegating from your agent" section in `README.md` documenting how a
  compatible agent (gentle-shell) delegates using the skill, with 1–2 example prompts
  (code → `run`, non-code → `ask`). Acceptance: the README documents the delegation flow;
  the prompts name the trigger and the mode.
- **S4** Q2: the "First run" example stops pointing at the toy repo `worker-poc-toy` and
  points at this repo (`--repo https://github.com/albertopo94/aistudio-bridge`); the
  `Output:` block is labelled *sample output*. Acceptance: the README does not reference
  `worker-poc-toy`; the output is labelled as sample.
- **S5** Q3: `-v` is an alias of `--version`. Acceptance: `remote-worker -v` prints the
  version and exits 0, pinned by a test.
- **S6** G9: `continue` against an expired sandbox must fail with a friendly message, not a
  raw 404. Design in `plan-remote-worker/README.md`, §"TODO — G9".
- **S7** G15 live proof: a continuation turn and a `continue` carry `system_instruction`
  and `tools` on the wire against the real API (unit tests only cover the fake server).
- **S8** Every behaviour change lands test-first with `python3 -m unittest discover -s
  tests` green, one reviewable commit per work unit, Conventional Commits, version bumped
  per AGENTS.md §3 (highest category per work unit, bump in its last commit).
- **S9** Nothing is pushed, merged or applied by the worker; push/PR/merge stay the user's
  decisions (AGENTS.md §5).
- **S10** (user, verbatim, 2026-11-06) "totalmente deacuerdo con lo que dices de 'traducir
  el 400 File size exceeds maximum allowed limit a un mensaje que diga lo que es: "la
  cuenta agotó su fair-use de la ventana; probá más tarde"'.también…" (rest lost to
  compaction; L26-L27 carry the full agreed design). Acceptance: `run --local-repo` makes
  **no local size refusal at all** — the service is the only authority on what the account
  accepts that day; when the service answers 400 "File size exceeds maximum allowed limit"
  the user reads that the account's fair-use window is exhausted, that retrying later is
  the action, and which routes remain. Pinned by a test against a fake server that returns
  that exact 400, and the removal of the old ceiling test is deliberate, not an oversight.
- **S11** One route table, one source of truth (L27.1). Acceptance: the routes and their
  measured limits/requirements live in exactly one data module; a CLI command prints that
  table; the skill text carries the same rows; no second copy of a number exists in code.
  The CLI stays non-interactive: it prints options, it never asks.
- **S12** `probe` (L27.2): a cheap live check that answers "can this account upload right
  now", not an estimate. Acceptance: it uploads ~1 MiB to a scratch environment, deletes
  the environment on every exit path including failure, reports ok/blocked in one line, and
  is covered by tests against a fake server (no test touches the network).
- **S13** The return path is a table too, with the same honesty as the upload one (L34).
  Rows, each with its real status: (a) recursive tar of `workspace` applied to a fresh
  local clone on a new branch — works, and today the only one the CLI reaches; (b) list the
  workspace before fetching anything — **implemented in `FilesClient.list_files`,
  unreachable from the CLI**; (c) fetch one file — **implemented in
  `FilesClient.download_to`, unreachable from the CLI**; (d) push a branch to GitHub —
  **rejected by design** (L33), documented with the reason rather than left silent.
  Acceptance: the table is printable offline like `routes`; the CLI can list and fetch a
  single file; tests cover both against a fake server; README and the skill embed the
  rendered table; the GitHub rejection states why in the docs.

- **S14** The download ceiling is **not measured** and must not be guessed (L35).
  Acceptance: the return-path table says `not measured` in words, and the feature doc names
  the test that would measure it and what it costs. Writing a number there is the same
  failure class as the deleted 1.5 GiB constant.

## Tasks

| ID | Spec | Route | Commit |
|---|---|---|---|
| T1 | S7 | live smoke — **user runs it** (consumes his API quota); parent decides from the output | docs(plan) |
| T2 | S3,S4,S5 | inline, test-first (Q1–Q3 self-test fixes, done locally, not delegated) | feat(self-test) |
| T3 | S2 doc-only | inline: G3, G8, G14/G27 costs, G16, G17, G19, G20, G21, G28 → README "Limits"/plan | docs |
| T4 | S6 | inline, test-first: G9 friendly error on `continue` | fix(g9) |
| T5 | S2 | inline: G1 `network` allowlist flags (unlocks G10 rule in docs) | feat(network) |
| T6 | S2 | inline: G2 `clean` — Environments list/delete + TTL guard | feat(clean) |
| T7 | S2 | inline: G7 `--reuse-env` | feat(reuse-env) |
| T8 | S2 | inline: G18 — probe `interactions.get`, then store/TTL behaviour | feat(get) |
| T9 | S2 | probe + decide: G22 part b (AGENTS.md as inline source in `.agents/`) | feat(agents-md) |
| T10 | S2 | inline: G11/G28 multimodal `--image` (single parts builder) | feat(multimodal) |
| T11 | S2 | **closed as covered** — no `--skill` flag: a skill reaching the sandbox via the official channel `/.agents/skills/<n>/SKILL.md` already works with `--mount-file LOCAL:TARGET`; skills are a local-agent concept (installed by `skill-installer`), the remote worker does not consume them, and a flag could not lift the service's 1 MB/file · 2 MB total limit (L42) | docs(closing) |
| T12 | S2 | probe + decide: G24 tools set for `run` (google_search) | feat(tools) |
| T13 | S2 | inline: G12 server-side cancel | feat(cancel) |
| T14 | S2 | writer candidate: G13 background + polling + SSE reconnect | feat(background) |
| T15 | dropped | inline: streaming SSE for `run` (parity with `ask`) | — (see L18) |
| T16 | S2 | PoC ~15 min: G5 upload ceiling 1.5 GB → decides whether G4 needs GCS. Script ready (`poc/g5_upload_ceiling.sh`); **live run is the user's** (consumes his API quota) | docs(poc) + v0.16.0 |
| T17 | S2 | **DONE inline** (writer unavailable): `run --local-repo` — archive the tree, create the env, upload with `extract=true`, guard at the measured 1.5 GiB | feat(local-repo) v0.17.0 |
| T18 | S2 | **closed by evidence** — G25 research returns HTTP 429 on the free tier (limit 0 input tokens/min), so it cannot run at all; G26/G29/G30 depend on it | docs(closing) |
| T19 | closing | **DONE inline** (docs only, no code): the plan gap index now tells the truth about G1-G30, and the two retracted numbers inside it are corrected. ADR 0001 stays the user's (AGENTS.md §9) | docs(closing) + v0.21.1 |
| T20 | S8 | inline: G19 — recognise a 429 and explain the no-retry rule (SSE currently loses the HTTP status) | fix(rate-limit) |
| T21 | S10,S11 | inline, test-first: route table as a data module + `routes` command + the fair-use 400 mapped to an actionable message; delete the fake 1.5 GiB guard | feat(routes) 98ec5cc + v0.18.0 d03b5ab |
| T22 | S11 | inline, test-first: `run --local-repo` reports upload progress through the `on_progress` callback already in `UploadClient` | feat(upload-progress) e3b4ca7 + v0.19.0 622d3a2 |
| T23 | S12 | inline, test-first: `probe` command | feat(probe) 25461f9 + v0.20.0 dfc60b4 |
| T24 | S11 | **quota OK (L42)** — live validation of sharded upload before it may be advertised; standing recommendation on skip stands, the user chose to run it | — |
| T25 | S11 | **DONE live (L44-L47)** — `--local-repo` end-to-end proven on the real service: archive→upload→agent turn→tar→branch on a clone of the RIGHT repo (task t_1794074888, 87k/34k tokens across attempts); `--delete-env` implemented (opt-in, only after a verified delivery) with the ask observed live | fix + feat 0.23.0 |
| T26 | S13,S14 | inline, test-first: the return path as a data table; expose `list`/fetch-one-file from the CLI (the client already has both); document the GitHub rejection and the unmeasured ceiling | feat(fetch) 3a71cac + v0.21.0 |
| T27 | S14 | **DONE** — measured live 2026-11-07 with user quota OK: bytes generated INSIDE the sandbox (zero upload window), single-file 1 GiB pass / 2 GiB 504, tar 1 GiB 504 / 3.6 GiB 500, ~122 s deadline; measured numbers replaced the three `not measured` cells. Bonus defect found and fixed by the same live pass: `--list` read the wrong size field (`sizeBytes` vs the real `size_bytes`), and a wrong path in a live sandbox was reported as sandbox expiry | feat(return-measured) + fix(size-field) + fix(404-cause) |

## Log

- **L1** (user, verbatim): "recuerdas que producto desarrollamos aqui?"
- **L2** (user, verbatim): "recordaba que habian mas G listados en "TODO" por hacer, me
  equivoque?" — confirmed: G1–G30 plus Q1–Q3, streaming for `run`, `agents.create`, ADR 0001.
- **L3** (user, verbatim): "sigue el orden que mejor consideres, me da igual siempre y
  cuando lo solventemos/implementemos todo."
- **L4** Order chosen by the orchestrator: live proof first (the only step needing the user's
  API quota, and the gate for trusting the code path everything else touches) → cheap docs →
  small code gaps by file locality → heavy features (background/stream/upload) → the one gap
  that is a product decision (G25, which costs money and needs an explicit yes).
- **L5** Context fact: `ask` is not idempotent with the memory tools — a prompt that forces a
  `memory_search` call creates a `requires_action` round trip, so the *next* turn is exactly
  the continuation G15 broke. That is the shape of the T1 probe.

- **L6** (evidence) Slice 1 on `feat/gaps-backlog` (3 commits, 376 authored changed
  lines): ee12efb docs(odd) feature spec; bf058d3 feat(client) request trace
  (`REMOTE_WORKER_TRACE`); 4654df7 feat(cli) `-v` alias + README delegation
  (Q1-Q3). 95/95 tests green. Q1-Q3 verified by acceptance (README no longer
  references `worker-poc-toy`; sample output labelled; prompts name trigger+mode).
- **L7** (blocker, outside this list): T1 (live G15 smoke) needs the user's API
  quota. Command given in conversation. Until it runs, T3+ continue on the branch.

- **L8** (evidence) T1 G15 live proof PASS against the real API. The user ran an
  `ask` that forced a `memory_search` round-trip with `REMOTE_WORKER_TRACE` set.
  The trace shows 2 turns: turn 0 (initial) and turn 1 (continuation,
  `previous_interaction_id` set) BOTH carry the 1052-char worker contract and the
  full tool set including the 3 `function` memory tools. Before v0.9.3 the
  continuation ran without contract or memory tools. This is the proof S7 asked for.

- **L9** (decision) G19 moved out of the docs-only task: its real fix is classifying
  the 429 in the client (SSE currently flattens the status to 0), not a README line.
  It joins the code tasks.
- **L10** (evidence) Slice 2, docs work unit (v0.10.0). Gaps closed as documentation
  from the 7 audits: G3 (worker reads text+image only), G6 (compaction ~135k vs
  `max_total_tokens`), G8 (sandbox stack, 4 cores/16 GB, startup ~5s), G14+G27 ("What
  a task costs": US$0 evidence for Antigravity, Google baselines US$0.25-3.25/US$5,
  Deep Research US$1-7 so the free-tier claim is scoped to Antigravity), G16 (two
  clocks table: sandbox 7d vs interaction 1d free/55 paid), G17 ("What Google keeps
  server-side" + why `store=false` is impossible for a resumable task), G20 (preview
  pin, symptom and one-constant fix), G21 (Argon outside the managed model set), G28
  (multimodal shape noted for G11), G10 (rule in README Security: delegate only
  against repos you own or trust). Version 0.9.3 -> 0.10.0 (MINOR: the trace and `-v`
  are new public surface). 95/95 tests green; README anchors and tables validated.

- **L11** (evidence) T4 G9 done test-first, v0.10.1. RED observed for the right
  reason: `InteractionsError: Interactions API error 404` escaping `continue_task`
  in 3 tests. GREEN: 4 new rules in tests/test_expired_task.py (friendly `expired`
  status, TTL named, budget echoed only when recorded, task marked non-resumable,
  exactly one attempt, non-404 still raised, CLI exit 3 with no useless `continue`
  advice). Suite 99/99. Differential check on the 4 sibling offline commands
  (status, status <id>, --version, -h): identical stdout/stderr/exit vs base.
  Process note: a sloppy edit briefly orphaned code at the end of `continue_task`
  and dropped `def status`; caught by `py_compile` + a `grep -n "def "` of the
  class, restored in the same slice before commit.
- **L12** (decision, delivery strategy) The branch now carries 824 authored changed
  lines across 8 commits, past the ~400-line guide. This feature is deliberately one
  long backlog, so the delivery strategy is: **each work unit is its own reviewable
  slice** (own commits, own CHANGELOG entry, own version bump, green suite at its
  commit), and review happens per work unit rather than per branch. If a PR is opened
  later it gets split at these boundaries; nothing is squashed. Recorded here because
  the slice guard measures the branch as a whole.
- **L13** (decision, routing) The remaining code units (G12, G18, G13, streaming for
  `run`, G1, G2, G7, G11/G28, G23) all edit `cli.py` + `runner.py` + `google_client.py`
  + tests, so their edit surfaces are NOT disjoint: parallel writers would overwrite
  each other and the parent's inline route would spend the remaining context budget on
  ~800 lines. Route: sequential task-mode worker deliveries of one work unit each, and
  the parent verifies the suite and commits each delivery before launching the next.
  Workers must confirm each endpoint's exact payload against the official doc before
  implementing (AGENTS.md §1: never invent an API).

- **L14** (defect in my own verification harness — read before trusting any earlier
  "differential check: SAME" in this Log) The differential runs compared base and new
  code with `PYTHONPATH=<base>` and `PYTHONPATH=<new>` while the shell sat in the repo
  root. `python3 -m` puts the CWD first on `sys.path`, ahead of `PYTHONPATH`, so BOTH
  sides imported the working tree and every comparison returned SAME. The earlier claims
  at L6 and L11 therefore proved nothing. Corrected harness: run each side from an empty
  temp directory (`cd $NULLDIR`) so `PYTHONPATH` decides the import, and prove it with a
  sanity line (base has no `--live`, new does). Re-run of the accumulated `main` (0.9.3)
  vs worktree (0.10.1+) over 9 offline commands: only `--version` (the bump) and `-h`
  (`--version, -v`) differ, which is exactly what the work unit intends.
- **L15** (evidence) T13+T8+T20 (G12/G18/G19) delivered inline, v0.11.0. Writer was
  launched once and failed (subagent model unavailable); per the no-retry rule the unit
  was done inline. RED observed per cause: `AttributeError: 'InteractionsClient' object
  has no attribute 'cancel_interaction'/'get_interaction'`, `KeyError: 'remote_canceled'`,
  `SystemExit: 2` for the unknown `--live`, and `'local tracking only'` still printed.
  GREEN: 16 new tests, suite 115/115 (was 99). Two test bugs fixed, not implementation
  bugs: constructing a client with an empty key (the real CLI builds no client at all
  without a key) and asserting the word "remote" where the CLI says "service".
- **L16** (contract correction) The gap index claimed `POST /v1beta/interactions/{id}
  :cancel`. The API reference names a path segment `/cancel` and no request body, and the
  response status is spelled `cancelled`. Also from the same pass, needed by T14/G13:
  background is a TOP-LEVEL boolean `background`, incompatible with `store=false` (400),
  there is NO `queued` status (running is `in_progress` / `requires_action`), polling is
  `GET /v1beta/interactions/{id}`, and an SSE stream can be resumed with
  `GET ...?stream=true&last_event_id=<id>`. Chaining to an `in_progress` interaction
  returns 400, which constrains how `continue` may meet a background task.

- **L17** (evidence) T14 G13 delivered inline, v0.12.0, 13 new tests (129/129). Three
  real findings inside the unit: (1) the fake server enqueued per-GET statuses but always
  answered reply_status — every queued GET status was silently ignored until the full
  suite caught it; (2) the CLI claimed "the agent reached its token budget" for a
  still-running continue — a false statement that also printed the exact command the
  tool had just refused, fixed with an explicit `reason` field instead of prose guessing;
  (3) the pre-check read in `continue` turned the sandbox-404 path into "conversation
  gone" one step earlier, pinned as its own test. Differential (correct harness): only
  run -h / ask -h / status -h differ from main, all new flags.

- **L18** (decision) T15 (streaming SSE for `run`) is **dropped**, not deferred.
  The memory function-calling loop cannot interleave with a one-shot stream: a
  `requires_action` round trip needs a `function_result` injected as the next
  turn, and the stream closes on the first turn. Disabling memory for a
  streaming run would remove the product's core design. `--background` (G13,
  v0.12.0) already solves the real failure it would have fixed (connection
  drops losing long-task results). Revisit only if the service documents SSE
  + `requires_action` interleaving.

- **L19** (incident, outside this list) Mid-session, the host's model router
  returned repeated 502s (rate_limited ×n, upstream_error, timeout, model_not_found,
  daily_quota_exhausted across all routed providers) and stalled the session.
  Provider-side, not repo work: the suite had gone 180/180 green and the G5/G4
  unit was on disk. State saved here + Engram (obs 2103) so a fresh session can
  resume: upload client, tar_gz, PoC script ready; G5 live run pending the
  user's command `bash poc/g5_upload_ceiling.sh`.

- **L20** (evidence) G5/G4 groundwork unit, v0.15.0 → 0.16.0. `UploadClient`
  in `remote_worker/google_client.py` (init/chunks/resume per the documented
  resumable protocol; `UploadResumeError` on an unusable `Range`; `UploadError`
  with status+body on a non-308 chunk reply), `tar_gz_bytes` in
  `remote_worker/sync.py`, 6 new tests in `tests/test_upload_client.py`
  (suite 180/180), and `poc/g5_upload_ceiling.sh` (1.5 GiB, 10 MB pre-cut
  slices, records the byte offset where a non-308 arrives, deletes the
  environment on exit — which also exercises the G2 DELETE). Review note:
  the first draft of the PoC sent the whole 1.5 GiB blob per chunk
  (`--data-binary @$BLOB`) — 225 GB of traffic and a meaningless
  measurement; caught before the user ran it, fixed with pre-cut slice
  files plus a total-size sanity check.

- **L21** (evidence, live) T16/G5 **measured**, not estimated. The user
authorized the run and the parent executed `poc/g5_upload_ceiling.sh` against
the real API (18m27s, 1.5 GiB): 153 chunks of 10 MB (1530 MiB) were accepted
and the final 1536 MiB chunk was rejected with HTTP 400 `{"error":{"message":
"File size exceeds maximum allowed limit.","code":"invalid_request"}}`. The
init call accepts any declared `X-Upload-Content-Length`, so the limit applies
on accumulation. **Verdict (b): the direct upload ceiling is ~1.5 GiB.** GCS
stays documented as the path above that range but is not part of this product
(no bucket in the profile), so the honest outcome is a readable refusal above
the measured ceiling instead of a silent failure. Environment deleted on exit,
which also exercised the G2 DELETE path.

- **L22** (defects found by the dry run, both fixed) Two real bugs in the PoC
surfaced only because a local fake server was run before spending quota:
(1) `POST /v1beta/environments` was sent with `{"type":"remote"}` and the API
rejects it — the `Environment` resource has no top-level `type` (only
`Source.type` does), so the body must be `{}`; (2) macOS `wc -c < file` pads the
count with leading spaces, which corrupted `X-Upload-Content-Length` and the
`Content-Range` denominator (`bytes 0-10485759/ 25165824`). Both were confirmed
fixed with two 1 MiB live runs (one plain, one `extract=true`) before the 1.5 GiB
run: create 200, init with `Location`, chunk 200, metadata 200, delete 200.

- **L23** (evidence, decision changed by measurement) T18/G25 **closed, not
implemented**. The plan carried research mode as a cost decision ("~US$1-3 per
task, needs an explicit user yes"). A single live probe replaced that estimate
with a hard blocker: `POST /v1beta/interactions` with
`agent="deep-research-preview-04-2026"`, `background=true` returns **HTTP 429**
`{"error":{"message":"Rate limit exceeded for model deep-research-pro-preview
(limit: 0 input tokens per minute on Free Tier). Please upgrade your tier at
https://ai.dev/rate-limit.","code":"too_many_requests"}}`. The free-tier limit
is literally 0 input tokens per minute, so the mode can never execute and
shipping it would be dead code for this project; only a paid tier enables it.
G26 (forced store), G29 (planning questions) and G30 (background for research)
depend on it and close with it. The contract facts needed the day the tier
changes are recorded in `plan-remote-worker/README.md` (agent_config type
`deep-research`, mandatory `background`, `Api-Revision: 2026-05-20`, the two
agent versions, the 3-turn collaborative planning flow) so nobody re-researches
them.

- **L24** (evidence) T17/G4 delivered inline, v0.16.0 → 0.17.0. The writer was
launched once and failed with a provider-side "unavailable", so per the no-retry
rule the unit was done in the parent (same handling as L15). No partial writes
were left behind: `git status` showed only the two doc files and the suite was
still 180/180 before the work started. What landed:
`sync.archive_repo()` (streams the tarball to a file, tracked + untracked-not-
ignored only, never `.git`), `sync.UPLOAD_LIMIT_BYTES` (1530 MiB, the size the
G5 run demonstrably took), `EnvironmentsClient.create_environment()`,
`UploadClient.upload_file()` (chunk-at-a-time reads) and
`UploadClient(upload/extract)` carrying `extract=true` on the init call only,
`runner` wiring that creates the sandbox, uploads to `workspace/<name>/` and
runs the interaction in that environment, plus `cli --local-repo` in a required
mutually exclusive group with `--repo`. 7 new tests (`tests/test_local_repo.py`),
suite **187/187**. Two defects found by those tests, both real: (1) `_quote_path`
stripped the target's trailing slash, which is exactly the signal that makes the
service unpack into the directory instead of creating one named after the file —
the upload target now restores it; (2) the first test run reached the *real* API
(401) because the test runner injected only the interactions client, not the
environments/uploads ones — the fake now covers all three surfaces and no test
touches the network.

- **L25** (incident, harness) The writer subagent failed on a provider outage
("assistant reported an error (unavailable)"), not on the task. Its task carried
no side effects, so nothing was replayed or relaunched; the unit continued
inline instead of retrying an unavailable model.

## Post-cierre: próxima idea (discutida, sin implementar)

**L26** (user, verbatim, 2026-11-06): "dejar todas las posibilidades de
subidas implementadas o documentadas ya sea en codigo o en skill y que el
agente le diga al usuario las que hay disponible y que el usuario escoja
con un enter [...] ya no es solo la velocidad lo que se puede evaluar,
tambien entra en juego la cantidad que google deja subir si considera que
se esta abusando o no, asi que puede que segun el momento y la infra
estructura del usuario se pueda usar una u otra, asi que nosotros
implementemos y probemos todas para saber los limites y requisitos de cada
via y que el usuario seleccione mas alla de lo que el agente/llm le pueda
sugerir."

**L27** (decision, diseño acordado — implementación en otra sesión):
selector de rutas de subida.
1. Tabla de rutas como **una sola fuente de datos**, leída por dos lados:
   el CLI la usa en los mensajes de error (un 400 fair-use →
   "ventana agotada; alternativas: --repo / GCS / reintentar mañana") y
   el SKILL lleva la misma tabla para que el agente local del usuario
   muestre el menú. La CLI sigue **no interactiva** (regla de producto);
   la selección con Enter vive en la capa del agente, donde está el
   humano. El LLM sugiere, el usuario elige.
2. Dos capas: **pre-flight** (gratis: tamaño estimado del tarball, host
   git alcanzable, bucket GCS configurado o no — filtra el menú a lo que
   es real) y **runtime** (el fair-use solo se mide intentando: comando
   `probe` de 1 MiB a un environment deschedable que diga "ahora se puede
   subir / bloqueado hasta que se resetee").
3. Rutas y números medidos a 2026-11-06: `--repo` (500 MB documentado,
   clon server-side) / `--local-repo` directa (ventana fair-use de la
   cuenta, no 1.5 GiB; ~1.1–1.6 MiB/s impuestos por el servicio; 500 MB ≈
   6 min) / shards (solo stdlib, **mismo** fair-use, no documentado por
   Google → validar en vivo antes de publicarlo) / GCS (requiere bucket
   del usuario, 2 GB documentado, ~5 min para 1.5 GB a 51 Mbps, la vía
   que se aleja del fair-use porque el transporte es distinto) / inline
   (1 MB por archivo, 2 MB total).
4. Orden de implementación acordado: (a) reemplazar el guard falso
   `UPLOAD_LIMIT_BYTES` por mapeo amigable del 400; (b) tabla como
   módulo de datos; (c) `probe`; (d) validar shards en vivo; (e) GCS solo
   cuando el usuario quiera meterle bucket.
5. Pendiente técnica: `on_progress` agregado al `UploadClient` tras
   0022faa; suite completa por correr antes del próximo commit.

**L28** (evidence, 2026-11-06): suite completa **187/187 OK** (`python3 -m unittest
discover -s tests`, 65s) sobre `a1dca98` — cierra el punto 5 de L27: el callback
`on_progress` quedó cubierto por la suite entera, no solo por los 13 tests de upload.

**L29** (evidence, medición en vivo pedida por el usuario): `/tmp/g5_timings.py` corrió
300 MiB y 20 MiB contra la API real (logs `/tmp/g5-timings-300.log`,
`/tmp/g5-timings-20.log`). Resultado que cambia el diseño:
- **300 MiB murió a los 290 MiB** con el mismo 400 "File size exceeds maximum allowed
  limit", y **poco después 20 MiB pasó**. Un techo por archivo no puede explicar eso: la
  ventana de fair-use de la cuenta es la que manda. `UPLOAD_LIMIT_BYTES = 1530 MiB` es un
  número falso como guard local (era válido el día de cuota fresca).
- Throughput impuesto por el servicio **1.06–1.63 MiB/s** (8–13 Mbit/s), no la línea del
  usuario (51.86 Mbps de subida). `init+first 5.12 s` vs `p50 5.84 s` → sin overhead
  notable por handshake: la idea de agrandar chunks no gana nada.
- Fases: payload en memoria 0.1–0.3 s; crear environment 3–6 s; cleanup 1 s; chunk p50
  ~5.8 s / p95 ~12 s con 10 MiB. Proyecciones útiles: 500 MB ≈ 6 min, 1 GB ≈ 13 min.
- Consecuencia implementada: S10 (borrar el guard local, mapear el 400) y S11 (tabla de
  rutas). T24 (shards) sigue bloqueado: el usuario pidió confirmar después de la medición
  y la medición mostró que los shards **no** resuelven el límite de ventana, solo harían
  reanudable una subida larga.

**L30** (evidence) T21 entregado inline, test-first, v0.18.0. RED observado por la
razón correcta (`ImportError: cannot import name 'routes'`). Lo que landed:
`remote_worker/routes.py` (tabla de 5 rutas con `ceiling`/`speed`/`needs`/`status`,
`format_routes()`, `alternatives_line()`, `is_size_limit()`, `upload_failure_message()`),
el comando `routes` offline en la CLI (no pide key: se lee antes de gastar cuota), el 400
"File size exceeds maximum allowed limit" traducido a ventana de fair-use con acción
(reintentar) y rutas alternativas, y el sandbox de un intento fallido se borra antes de
levantar el error (si el delete falla, el mensaje lo dice y nombra `clean --yes`). Se
**borró** `sync.UPLOAD_LIMIT_BYTES` y su guard: el número de 1530 MiB era verdadero el
día medido y falso esa misma tarde (300 MiB rechazado a los 290, 20 MiB aceptado minutos
después); el test que lo imponía se borró a propósito y `test_the_fake_local_ceiling_is_gone`
fija que nadie lo reintroduzca. Una sola fuente de verdad: la tabla renderizada vive en
`routes.py`, la imprime la CLI, el SKILL la embebe y el README también —
`test_the_readme_embeds_the_rendered_table` y `test_the_skill_embeds_the_rendered_table`
rompen si alguna copia se corre. 12 reglas nuevas en `tests/test_routes.py`; suite
**198/198**. Chequeo diferencial contra `1b459da` en 9 invocaciones (`--version`, `-v`,
`-h`, `run/ask/clean/setup/status/continue/cancel -h`): idénticas salvo `-h`, que agrega
el subcomando nuevo — lo pedido, nada no pedido. `routes` sale con `rc=0` y sin red.

**L31** (evidence) T22 entregado inline, test-first. RED: 3 failures por ausencia de
líneas de progreso en stderr. Lo que landed: `_upload_progress()` en `runner.py`
devuelve el callback que por fin consume el `on_progress` que ya existía en
`UploadClient` (commiteado en `a1dca98`, sin consumidor hasta ahora). Formato:
`upload <sent>/<total> bytes at <rate> eta <t>` y línea final `... done in <t>`.
Tres decisiones, cada una con su test: el progreso va a **stderr** (stdout sigue
siendo el reporte, así `> report.txt` sigue sirviendo); la tasa se mide de la subida
actual, no de un promedio recordado; y ningún byte que el servicio no confirmó aparece
como enviado (test con una ventana que corta a los 2 chunks). Suite **202/202**
(`tests/test_upload_progress.py`, 4 reglas). Commit e3b4ca7.

**L32** (evidence) T23 (`probe`) entregado inline, test-first. S12: la ventana de
fair-use no se puede leer de ningún lado — solo se mide intentando — y intentar con un
payload real cuesta minutos. 1 MiB a un sandbox desechable contesta lo mismo en un
segundo.

RED observado: la primera corrida dio 4 errores. Dos causas, y hay que nombrar las dos:
una era mía y no del producto — mi fake server contestaba `200` a un chunk no final,
cuando el protocolo pide `308`, así que el cliente lanzaba `UploadResumeError` antes de
llegar a lo que quería probar —; la otra era un defecto real de mi implementación: el
`finally` escribía sobre un `outcome` que aún no existía cuando el environment fallaba
(`UnboundLocalError`), o sea la ruta de limpieza no estaba cubierta. Corregido el fake
(habla el protocolo: `308` + `Range` hasta el último chunk) e inicializado `outcome`
antes del `try`, las 6 pasaron sin tocar ninguna aserción.

Lo que landed: `TaskRunner.probe()` crea el environment, sube `PROBE_BYTES` (1 MiB) a
`PROBE_TARGET` y **borra el sandbox en todo camino de salida** (incluido el 400 y un
client roto) desde el `finally`; no crea interacción alguna — gastar cuota de
interacción para preguntar por bytes es la factura equivocada. El CLI imprime una línea
con bytes, tiempo y tasa, y sale `0` (sube) / `4` (ventana agotada: el mismo código del
429 porque es la misma clase de respuesta — esperar, no arreglar) / `1` (lo demás). Si
el sandbox no se pudo borrar, el mensaje lo dice y nombra `clean --yes`.

Las 6 reglas de `tests/test_probe.py`, cada una con su evidencia: funciona → ok con tasa
y DELETE del environment; ventana agotada → rc 4 y la palabra "fair use"; **cero**
llamadas a `/v1beta/interactions`; bytes subidos ≤ 2 MiB; delete rechazado → avisa;
`probe` no pide argumentos. Formateo humano (`human_bytes`/`human_rate`/
`human_duration`) vive en `routes.py` para que el progreso y el probe hablen igual.
Suite **208/208**. Chequeo diferencial contra `1b459da` en 12 invocaciones: `run/ask/
clean/setup/status/continue/cancel -h` y `status nope` idénticas; difieren `-h` (agrega
`routes` y `probe`), `--version`/`-v` (0.17.0 → 0.19.0, del bump ya commiteado) y
`status` sin argumento, que lee el estado local del usuario — no una diferencia de
código. Ninguna diferencia no pedida.

**L33** (discovery + disposition, user question 2026-11-06) "en esa via tienes
contemplado github? ... Pense que existia esa via. Me equivoque todo este tiempo?"
**Verificado en código para responder: sí se equivocaba, y es bueno que lo haya
preguntado.** La fuente `repository` (`--repo <url>`) no tiene soporte de
credenciales en ninguna parte del producto (ni sync.py, ni planner.py, ni
google_client.py envían token/PAT alguno): el sandbox clona solo URLs que puede
alcanzar por sí mismo, o sea **públicas**. Los repos privados del usuario nunca
pudieron ir por GitHub: siempre fueron por `--local-repo` (o por un espejo
público). La vía "subir a GitHub para que Google lo baje" entonces **sí existe
para repos públicos** (`--repo` es exactamente eso) y **no existe para privados**
— que es probablemente lo que el usuario asumía. Como salida ("que Google pushee
el resultado"): rechazado por diseño desde la fase 2 — requeriría entregar al
sandbox una credencial con escritura sobre el GitHub del usuario; AGENTS.md §8
define que el worker nunca recibe credenciales de escritura y que la verdad de
git vive en la máquina del usuario. El flujo que vuelve el trabajo es: rama local
nueva → el usuario revisa → él pushea con sus credenciales. El usuario expresó
que esa parte "no suena nada mal". Queda como fila (d) de la tabla de bajada:
rechazada a propósito y documentada con el porqué, no silenciosa (S13).

**L34** (decision) La bajada merece la tabla igual que la subida (S13), pero la
simetría con la subida es un falso amigo: la subida tiene 5 rutas porque depende
de **dónde viven los archivos** (¿host git? ¿solo disco? ¿bucket?); la bajada
tiene **un solo transporte** porque el sandbox es un filesystem sin memoria de
cómo se llenó, y la única primitiva documentada es el endpoint de files (tar
recursivo). El eje que sí faltaba poblar es la **granularidad**: hoy se baja el
workspace completo en tar siempre (`runner.py:353` es el único call site de
`FilesClient`), mientras `list_files` y `download_to` de-un-archivo están
**implementados en el cliente y son inalcanzables desde la CLI** — el defecto de
"implementado y enterrado". T26: tabla de bajada + exponer listar/bajar-uno en
la CLI, test-first, y documentar el rechazo de GitHub push.

**L35** (open technical question, en el tintero por pedido explícito del usuario)
El **techo de bajada no está medido** y S14 prohíbe adivinarlo (es la misma clase
de error que el 1.5 GiB borrado). Corrección que me puse a mí mismo en la
conversación: medirlo honesto **no** es gratis ni "segundos" — hace falta un
payload grande dentro del sandbox, y eso se consigue (a) subiendo un archivo
grande → gasta la ventana de fair-use, o (b) haciendo que el agente genere
archivos → gasta cuota de tarea y minutos. La variante barata (bajar el workspace
de un sandbox con pocos MB) solo prueba lo chico. T27 bloqueado hasta OK del
usuario; al correrlo hay que declarar qué número queda sin medir.

**En el tintero, pregunta abierta del usuario (2026-11-06): ¿shards y GCS están
diseñados y/o probados?** Estado exacto a hoy: **ninguna de las dos está
implementada ni probada en vivo**. *Shards*: diseño existe (trozar el tar, subir
N partes, recortar/rearmar en un turno de sandbox con `cat` + `tar -xzf`), cero
código, cero pruebas; Google no la documenta; su valor real sería solo hacer
reanudable una subida larga — **no** levanta la ventana de fair-use (medido,
L29). Su fila en la tabla dice `not validated live` a propósito. *GCS*: solo
documentada (2 GB, requiere GCP + bucket + gsutil del usuario), cero código, el
usuario no declaró bucket. Ninguna se ofrece como alternativa en un mensaje de
error. Decisión pendiente del usuario: validar shards en vivo (T24, gasta cuota)
o dejarlos como fila informativa y, si algún día hace falta >1.5 GiB, ir directo
a GCS cuando quiera meterle bucket.

**L36** (user disposition, 2026-11-06) El usuario cerró el tema del PR: la única
pregunta era si Google puede subir el resultado a su GitHub, y la respuesta es no
(ninguna credencial entra al sandbox, L33). Lo que su agente local hace después
con `gh` (push, `gh pr create`) es comportamiento normal de su lado y **no se
documenta, no se implementa, no se imprime como sugerencia desde el tool**: quedó
explícitamente descartado, para que ninguna sesión futura lo re-proponga como
feature. El flujo del tool termina en la rama local y ahí muere su alcance.

**L37** (evidence) T26 entregado inline, test-first, v0.21.0. RED observado por la
razón correcta (`AttributeError: module 'remote_worker.routes' has no attribute
'format_return_routes'`; 21 tests, 17 errores + 1 fallo). Dos ajustes que hay que
nombrar para que el verde valga: los aflojé **antes** de implementar, no después
de ver el fallo, y en los dos la aserción quedó más estricta que la original —
`method_paths()` compara strings completos, así que `assertIn` de un prefijo no
aplica (se pasó a la ruta exacta con `?recursive=true`), y la idea de un
`return_alternatives_line()` se cambió por verificar que ninguna fila
`refused by design` aparece entre las usables, que es la regla de S11 y no una
función nueva que nadie pide.

Lo que landed: `RETURN_ROUTES` en `routes.py` (4 filas, los mismos campos que la
de subida: `ceiling`/`speed`/`needs`/`status`), `format_return_routes()`,
`return_routes_by_id()` y `STATUS_REFUSED`; `routes` imprime las dos tablas y
sigue offline. En el runner, `list_workspace()` y `fetch_file()` sobre el
`FilesClient` que ya existía: todo lo resoluble localmente (task desconocida,
task sin sandbox, archivo local que existe) se resuelve **antes** de mandar la
petición, así un error de tipeo cuesta cero. `fetch` en la CLI con `--list`,
`--path`, `--out`, `--overwrite`; un directorio como `--out` es destino, no
archivo a pisar; la bajada de un archivo usa `recursive=False` porque es un
archivo, no un árbol a tar. El 404 se lee como sandbox expirado (TTL 7 días) con
la acción y sale `3`, igual que G18/G9 en su forma.

Lo que **no** se hizo a propósito: tres techos dicen `not measured` en palabras
(S14). Medirlos honesto necesita un árbol grande adentro del sandbox primero, y
eso gasta ventana de subida o cuota de tarea (T27 sigue bloqueado esperando OK
del usuario). Escribir un número ahí sería la misma clase de error que el
constante de 1.5 GiB que este proyecto ya borró.

Tests separados por interés, como en el resto del repo: `tests/test_return_path.py`
(la tabla, sus filas, sus tres lectores) y `tests/test_fetch_command.py` (la CLI
contra un fake del endpoint de files, `alt=media` en la bajada y ausente en el
listing). Suite **230/230** (80.7s). Chequeo diferencial contra `ce97554` en 12
invocaciones offline, con el harness corregido de L14 (cada lado desde un
directorio vacío, `PYTHONPATH` decidiendo el import, y línea de cordura mostrando
que base no conoce `fetch`): difieren solo `-h` (el subcomando nuevo, +7/-4) y
`routes` (la tabla de bajada agregada, +24/-0). Las otras 10, idénticas en
stdout, stderr y código de salida. El README además deja de afirmar que la ruta
de subida está "not implemented yet", que era falso desde v0.17.0.

## Slice boundaries (L12 applied, 2026-11-07)

La rama pasó hace rato la guía de ~400 líneas authored lines y lo hizo a propósito:
esto es un backlog largo, así que la unidad revisable es el **work unit**, no la
rama. Cada uno trae su commit de comportamiento con tests y docs juntos, su entry de
CHANGELOG, su bump y la suite verde en ese commit. Si algún día se abre PR, se corta
en estas líneas y nada se aplasta.

| Slice | Work unit | Commits | Versión |
|---|---|---|---|
| 1 | Q1–Q3 self-test + request trace | ee12efb..4654df7 | sobre 0.9.3 |
| 2 | docs de gaps (G3/G6/G8/G14/G16/G17/G20/G21/G28/G10) | docs work unit | 0.10.0 |
| 3 | G9: 404 del sandbox expirado, amigable | T4 | 0.10.1 |
| 4 | G12 + G18 + G19 (cancel, get, 429 sin perder estado) | T13/T8/T20 | 0.11.0 |
| 5 | G13 background + polling + watch | T14 | 0.12.0 |
| 6 | G5 medición en vivo + `UploadClient`/`tar_gz` + PoC | T16 | 0.15.0→0.16.0 |
| 7 | G4 `run --local-repo` | T17 | 0.16.0→0.17.0 |
| 8 | S10+S11 tabla de rutas, 400 fair-use traducido, guard falso afuera | T21 · 98ec5cc, d03b5ab | 0.18.0 |
| 9 | progreso de subida visible en stderr | T22 · e3b4ca7, 622d3a2 | 0.19.0 |
| 10 | `probe` (ventana de subida medida en 1 s) | T23 · 25461f9, dfc60b4 | 0.20.0 |
| 11 | trazabilidad tarea→commit en este doc | ed41223 | 0.20.1 |
| 12 | S13+S14 tabla de bajada + `fetch` + rechazo de push documentado | T26 · 3a71cac, fc874ac, db3d3fe | 0.21.0 |
| 13 | T19: el índice del plan dice la verdad (G1-G30 por cómo cerró) + 2 claims podridos corregidos | T19 · 9f97287, 21f69f4 | 0.21.1 |
| 14 | fix `fetch`: campo de tamaño real (`size_bytes`) + causalidad del 404 (path vs expirado) | L40 · ee7b0e1, 0252556 | 0.21.2 |
| 15 | T27: techo de bajada medido en vivo; celdas `not measured` reemplazadas por números con su error | T27 · (feat) + bump | 0.22.0 |
| 16 | F1: tarea grabada `completed` aunque la bajada falló → estado `undelivered` + `clean` la protege + `continue` la rechaza + CLI imprime la recuperación | L41 · (fix) + bump | 0.22.1 |
| 17 | T11/G23 cerrado como cubierto (docs) + decisiones de sesión (GCS aprobado para construir, OK cuota T24/T25) + la línea "Sigue abierto" del plan deja de presentar a T27 como sin medir | L42 · docs + bump | 0.22.2 |
| 18 | F2: la protección de `clean` abarca TODAS las cuentas locales (cross-project) + corrección del claim falso en L41 | L43 · fix + bump | 0.22.3 |
| 19 | Dos defectos del primer run en vivo de T25: el shape del `environment` en las rutas por id (L44) y el clone viejo que absorbía deliveries ajenos (L45) | L44, L45 · fix + bump | 0.22.5 |
| 20 | `--delete-env`: borra el sandbox solo tras delivery verificado, y si no se pasa el flag, la pregunta (TTY) o el comando (agente) | L46 · feat + bump | 0.23.0 |

**Corte de review (L47):** los commits hasta `437cb9f` (0.22.1 → 0.22.5,
T11/G23 + F2 + los dos defectos de T25) son la slice A; `--delete-env` y lo
que siga (T24, GCS) abre la slice B. Si algún día se abre PR, se corta ahí.

Lo que queda abierto, con su dueño (estado al L47): **T24** (validar shards en
vivo) — OK de cuota (L42), pendiente de ejecución; **gcs-source** aprobada
para construir (L42: requiere el proyecto GCP + bucket del usuario, y el
setup se documenta para cualquier usuario que no lo tenga); **ADR 0001**
tiene borrador en `docs/adr/` pendiente de la firma del usuario
(AGENTS.md §9). T25 quedó cerrado en vivo (L47) y ninguna ruta sin prueba en
vivo se anuncia como disponible.

**F1 (defecto nuevo, encontrado midiendo T27, dueño `run`):** cuando la bajada
del tar falla (tar > ~1 GiB, o un workspace que el agente llenó de artefactos),
la tarea ya quedó grabada `completed` con reporte y tokens, el branch nunca se
crea, y el CLI escupe el 500/504 crudo del servicio. Estado y entrega
discrepan: `completed` sin resultado entregable. **FIX (L41):** el retorno se toma como unidad con resultado declarado.
`run` envuelve `download_to` + `apply_to_branch` en un try que captura el error
del servicio (500/504), el timeout, un tar ilegible y un git fallado, y graba
la tarea como `undelivered` — un estado nuevo que no está en
`FINISHED_STATUSES`, así que `clean` NO borra el sandbox (es la única copia de
los bytes) y `continue` la rechaza (el turno terminó y se pagó). El CLI
imprime la recuperación exacta (`fetch <task> --list`, `--path <file>`), el
TTL de 7 días, el error del servicio, y sale 3; un half-archive nunca queda en
el workdir. Evidencia original: t_1794045414 (3.6 GiB), 93.446 tokens,
`status: completed`, branch `worker/t27-download-ceiling` inexistente.
Tests: tests/test_delivery_failure.py (6 reglas, incl. el camino sano con tar
real + clone real). Suite 239/239.

- **L38** (evidence) T19 done. The task read "the gap index still shows every G1-G30
  row in its original 'to do' wording", and that was true: the index sorted gaps by
  *priority as of 2026-11-05*, so G2, G4, G5, G7, G11, G22, G23, G24, G25 all read as
  pending while the code had them closed for weeks. Replaced the priority table with a
  **how-it-closed** table (code / doc / closed-by-live-evidence / dropped / still open)
  and marked the three "Cobertura doc" audit sections and the "Próxima sesión" plan as
  dated records rather than open work. Two stale claims found while doing it, both
  worse than a missing checkmark because they assert something the code no longer does:
  (1) the closing table credited G4 with a "guard against the measured ceiling", but
  that guard (`UPLOAD_LIMIT_BYTES = 1530 MiB`) was deleted in v0.18.0 — the row now says
  the guard was removed and why; (2) the closing table and its footer stated the upload
  ceiling as a fact ("Techo real 1.5 GiB"), which is the exact claim the fair-use
  measurement retracted the same evening it was made — both now state the window, not
  the number. One honesty correction against my own first draft: I wrote that the G9 404
  "was reproduced against the real API before writing code" (that is step 1 of the
  design); L11 shows the reproduction was against the fake HTTP server, so the note says
  that and names the missing live run instead of claiming it. `Estado` section updated
  (v0.21.0, 42 commits, the real command list, 230 tests). No code touched: suite 230/230
  before and after, and docs-only means PATCH per AGENTS.md §3.

- **L39** (evidence) T27 measured, with the user's explicit quota OK. The method
  dodged the cost that had blocked it: instead of uploading a big tree (spending
  the fair-use window), one cheap `--background` flash task was asked to `dd`
  four files (100/500/1000/2000 MiB) into /workspace/bigfiles inside the sandbox.
  The task turn died at the very end — service HTTP 500 — but the sandbox kept
  the 3.6 GiB, so the measurement ran entirely on free reads. Numbers, all
  against the tool's own transport: single file 100 MiB/56.6s, 500 MiB/253.4s,
  1 GiB/348.9s (~1.8-2.9 MiB/s, bytes verified equal to declared); 2 GiB refused
  504 deadline_exceeded at 121.8s (reproduced twice, harness timeout ruled out
  by a 3600s client). Recursive tar: 1 GiB 504 at 121.6s, 3.6 GiB 500 at 123.0s.
  Every failure lands at ~122s whatever the payload: a deadline in TIME, not a
  cap in bytes, so the table names pass/refuse pairs and what stays unmeasured
  instead of inventing a line (S14). The root cause of the 500 that killed the
  task turned out to be the same 3.6 GiB tar: the task recorded `completed` with
  a full report (93k tokens), but the branch never appeared because the failure
  happened in the return download AFTER recording. That is a real defect (state
  says completed, delivery did not happen; the raw 500 escapes the CLI) recorded
  here as follow-up F1, owned by `run`, not by this table.
- **L40** (evidence) the same live pass found two defects in `fetch` and both
  went test-first. (1) The listing reader asked for `sizeBytes`; the service
  answers `size_bytes` (verified over 12 live environments) — so `--list` never
  printed a single size, and the test fixture had invented the same wrong name,
  green over a broken command. RED observed with the real shape (no size line
  printed), fixture corrected, reader now takes size_bytes first; a DIRECTORY
  entry prints no size because it has none. (2) A 404 from the files endpoint
  means "path not found" OR "sandbox expired" and the bodies are identical
  (both shapes captured live, same account, one dead + one live environment);
  the reader always said expired, sending users to re-run tasks whose sandboxes
  were fine. Now the root listing (free, metadata) tells them apart: live+empty
  = expired (exit 3, unchanged), live+entries = `no such path` + what exists
  one level down + exit 2, unreadable root = refuse to guess. Fake server
  learned both live shapes, including that a dead sandbox does NOT 404 at root
  (200 with nothing inside). Suite after: see commit.

- **L41** (evidence) F1 fixed test-first. RED first, observed: with a fake that
  answers 500 on the workspace download, `run` raised the raw
  `InteractionsError` through the record it had just written as `completed`
  (and a corrupt tar raised `tarfile.ReadError` the same way). GREEN: the
  return path is one unit with a declared outcome. `run` catches the download
  and the apply failures (service error, timeout, unreadable tar, git failure)
  and records `undelivered` — a status that is deliberately NOT in
  `FINISHED_STATUSES`, because `clean --yes` deletes the sandbox of every
  finished task, and for this one the sandbox is the only copy of the work the
  agent already paid for. `continue` refuses it (the turn is over; its guard
  already rejects anything that is not `incomplete`), and the CLI prints the
  recovery — `fetch <task> --list`, `fetch <task> --path <file>` — with the
  7-day TTL, the service's own error, and exit 3. A partial tar is removed in a
  `finally`, so a failed download no longer accumulates a garbage archive per
  task. The healthy path is pinned by a test that uses a REAL tar and a REAL
  local clone, not mocks, so the try/except cannot silently swallow delivery:
  the branch appears, the diff shows `main.py`, and the tar is cleaned up.
  The old evidence task t_1794045414 still shows `completed` in its state
  record; it was created before the fix and is left as-is. Its sandbox
  still holds the 3.6 GiB if the user wants to recover it, but because the
  record reads `completed` (a finished status) `clean` treats that sandbox
  as finished work: the F1 protection applies to tasks recorded
  `undelivered` from now on, not to this pre-fix record (corrected L43:
  the first draft claimed `clean` keeps it; a live dry run on 2026-11-07
  showed it listed for deletion). Suite 239/239.

- **L42** (decision) Session resume (2026-11-07, evening). The four open items
  came back with decisions: (1) T11/G23 — the user delegated the disposition
  to the parent's product judgment; (2) gcs-source — **approved to build**
  (the user will provide the GCP project + bucket; live validation waits on
  that); (3) T24 + T25 — quota OK for both live runs; (4) ADR 0001 — the user
  asked what it was: it is the record of the decision made on 2026-11-04 that
  aistudio-bridge is a standalone product, not a part of agy-model-bridge
  (plan, Decision 1); per AGENTS.md §9 the human writes it; a draft offer was
  pending at close of this entry.

  **T11/G23 closed as covered** (no `--skill` flag): the capability G23 asked
  for — a skill reaching the sandbox through the official inline channel
  `/.agents/skills/<name>/SKILL.md` — already exists and works via
  `--mount-file LOCAL:TARGET` (any text file, any target path; the gap index
  has carried G23 as closed since v0.14.0 and T19's index re-confirmed it).
  A dedicated flag would be new public CLI surface for a local-agent concept:
  in this product the skill is installed LOCALLY by `skill-installer` so the
  local agent learns to call the CLI — the remote Antigravity agent does not
  consume the user's skill packs. And the sugar could not lift the service's
  real constraint (1 MB per file, 2 MB total): a `--skill` flag would promise
  what the service will not keep. The product's remaining value is on the GCS
  route and the live validations, so no surface is spent on the convenience.
  Docs-only close; no code touched.

- **L43** (defect) F2 found while preparing the storage-quota cleanup. The
  T25 live run died on a new 429: "Project environment storage quota
  exceeded" — the account's environment storage is full, and the 23 held
  sandboxes (housekeeping since L41) are the cause. Preparing the `clean
  --yes` that frees it, the dry run exposed the defect: `clean` lists
  environments from the SERVICE side, which is account-scoped (the
  Environments API has no per-project scope), but its protection set
  (`in_use`) only read the CURRENT project's store. Running `clean` from
  project A could therefore delete the sandbox of an UNFINISHED task in
  project B. Live proof: `clean` from aistudio-bridge listed
  `23a912d6ab17161af94ad26e33895e27` for deletion even though it belongs to
  a `requires_action` task in the worker-poc-toy project. FIX (test-first,
  rule 10 in tests/test_network_and_clean.py): the protection set is now
  built from every local project store under the state root
  (`list_project_stores` in state.py), so unfinished work is never offered
  for deletion no matter which project the CLI runs from. This is what
  makes the cleanup safe to run. Suite 241/241.

- **L44** (defect) T25, second live attempt, after the storage quota was
  freed (L43): the upload worked, then the service answered 400 "Provide a
  'type' field (for EnvironmentConfig) at 'environment'". The API reference
  showed the root cause: the interaction's `environment` field takes a
  string (an environment id) or an `EnvironmentConfig` whose `type` is
  required (value `remote`); `--local-repo` sent a bare `{"id": ...}`
  object and `--reuse-env` sent `{"id": ..., "sources": ...}`. Neither form
  exists in the reference. `--local-repo` had never been run live (that is
  what T25 is for) and the fake accepted any shape, so the suite was green
  over a broken command -- the L40 lesson again. FIX (test-first, commit
  33f53d0): `--local-repo` passes the id string; `--reuse-env` uses the
  documented `{type: remote, environment_id, sources}` form, which updates
  the sandbox instead of creating one; the fake now answers the exact 400
  for an object without `type`. Suite 241/241.

- **L45** (defect) T25, third live attempt (with L44 fixed): the run
  completed and exited 0, but the branch landed in a clone of a DIFFERENT
  repository. `apply_to_branch` (sync.py) cloned only when the clone
  directory had no `.git`, and the clone path is fixed per project: the
  worker-poc-toy clone from the PoC era was still there, so the delivery
  was checked out on ITS history and overlaid, while the real repository
  (the scratch path) never received a branch. The tests never caught it
  because every test starts with a fresh tempdir workdir. FIX (test-first,
  StaleCloneTest): the clone is used only when its origin is that exact
  repository; otherwise the old clone is kept under a dated backup name
  (it may hold a branch nobody merged yet) and the right one is cloned.
  Live evidence: the run's output named `.../work/clone` with branches
  `worker/worker-poc-toy-*`, and the scratch repo had no `worker/t25-e2e`.
  Suite 249/249.

- **L46** (feat) `--delete-env` (T25's product decision, L42): the user
  wants the tool to ask, and would delete whenever the repo is up to date
  locally (delivered) or on GitHub. Implemented as: `--delete-env` deletes
  the sandbox only after a VERIFIED delivery, and never on `undelivered`
  (the sandbox is then the only copy); without the flag, a delivered run
  offers the question -- in a terminal it asks `[y/N]` with keep as the
  default, and when an agent drives the CLI it prints the same question
  with the `clean` command. A failed delete is reported and does not turn
  a delivered run into a failure. README documents the flag and the
  question. Tests: tests/test_delete_env.py (6 rules).

- **L47** (evidence) T25 proven live end-to-end, 4th attempt (the first with
  both fixes). Scratch repo `/tmp/t25-e2e.Wz9Fdg` (`pkg/ledger.py` +
  README, no tests) archived and uploaded (696 B); task t_1794074888 ran in
  the sandbox (34,074 tokens, status `completed`); the workspace tar came
  back and landed on branch `worker/t25-e2e` of a clone whose origin is the
  scratch repo (`/tmp/t25-e2e.Wz9Fdg`) -- F3's fix in the real world. The
  staged diff is exactly the task: one-line docstrings on every function of
  `pkg/ledger.py`, bodies untouched (changes are staged, not committed, so
  the user reviews before merging -- that is the design: git truth never
  leaves the machine and the scratch repo itself stays untouched). The CLI
  also printed the sandbox question (`sandbox 37c5130a... is kept for 7
days; to release finished sandboxes: remote-worker clean`), which is
  --delete-env's asked form observed live. The exit code was 1 only because
  the fixture repo has no `tests/test_main.py` (the fixed DEFAULT_TEST_COMMAND
  the worker contract names); on a repo with tests the CLI reports their
  result (L46). T25 is DONE.
