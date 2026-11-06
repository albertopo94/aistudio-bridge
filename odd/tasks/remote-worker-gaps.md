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
| T11 | S2 | inline: G23 `--skill` inline sources | feat(skills) |
| T12 | S2 | probe + decide: G24 tools set for `run` (google_search) | feat(tools) |
| T13 | S2 | inline: G12 server-side cancel | feat(cancel) |
| T14 | S2 | writer candidate: G13 background + polling + SSE reconnect | feat(background) |
| T15 | dropped | inline: streaming SSE for `run` (parity with `ask`) | — (see L18) |
| T16 | S2 | PoC ~15 min: G5 upload ceiling 1.5 GB → decides whether G4 needs GCS. Script ready (`poc/g5_upload_ceiling.sh`); **live run is the user's** (consumes his API quota) | docs(poc) + v0.16.0 |
| T17 | S2 | **DONE inline** (writer unavailable): `run --local-repo` — archive the tree, create the env, upload with `extract=true`, guard at the measured 1.5 GiB | feat(local-repo) v0.17.0 |
| T18 | S2 | **closed by evidence** — G25 research returns HTTP 429 on the free tier (limit 0 input tokens/min), so it cannot run at all; G26/G29/G30 depend on it | docs(closing) |
| T19 | closing | docs: mark every gap closed in the plan index; ADR 0001 reminder to the user | docs(closing) |
| T20 | S8 | inline: G19 — recognise a 429 and explain the no-retry rule (SSE currently loses the HTTP status) | fix(rate-limit) |
| T21 | S10,S11 | inline, test-first: route table as a data module + `routes` command + the fair-use 400 mapped to an actionable message; delete the fake 1.5 GiB guard | feat(routes) 98ec5cc + v0.18.0 d03b5ab |
| T22 | S11 | inline, test-first: `run --local-repo` reports upload progress through the `on_progress` callback already in `UploadClient` | feat(upload-progress) + v0.19.0 |
| T23 | S12 | inline, test-first: `probe` command | feat(probe) + v0.20.0 |
| T24 | S11 | **blocked** — live validation of sharded upload before it may be advertised; needs the user's quota OK | — |
| T25 | S11 | **blocked** — live end-to-end `--local-repo` run + `--delete-env` decision; needs the user's quota OK | — |

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
