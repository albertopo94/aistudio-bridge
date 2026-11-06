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
| T15 | pending | inline: streaming SSE for `run` (parity with `ask`) | feat(stream-run) |
| T16 | S2 | PoC ~15 min: G5 upload ceiling 1.5 GB → decides whether G4 needs GCS | docs(poc) |
| T17 | S2 | depends on T16: G4 resumable upload for repos > 500 MB | feat(upload) |
| T18 | S2 | last, needs a user product decision on cost: G25 research mode (+G26,G29,G30) | feat(research) |
| T19 | closing | docs: mark every gap closed in the plan index; ADR 0001 reminder to the user | docs(closing) |
| T20 | S8 | inline: G19 — recognise a 429 and explain the no-retry rule (SSE currently loses the HTTP status) | fix(rate-limit) |

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
