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
