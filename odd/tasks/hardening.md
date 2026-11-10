# Feature: harden the remote-worker bridge against its own review findings

Standalone product `remote-worker` (repo `aistudio-bridge`, main @ e2250db, v0.24.0).
The review notes in `puntos a mejorar.md` (two external reviews, weeks old) were re-checked
against the current code (2026-11-09, explore agent + parent spot-checks): 9 claims STILL
EXIST, 1 already fixed (remote cancel), and the clone-mismatch half of C2 is closed by F3.
This feature fixes the surviving findings, one work unit at a time.

## Specs

- **S1** (source, verbatim, `puntos a mejorar.md` §1-Crítico 1): "no ejecutar automáticamente
  las pruebas de código remoto en el Mac. Primero debe existir una revisión, y preferiblemente
  una ejecución dentro de un contenedor o entorno aislado, sin acceso a secretos personales."
  Design decision (parent, 2026-11-09): no container on the macOS stdlib-only stack; the
  bridge stops running remote tests automatically and instead offers
  `--run-tests` (opt-in) plus environment scrubbing on every run.
- **S2** (source, verbatim, §1-Crítico 4): "No mantendría ese fallback: exigiría una
  extracción segura en todas las versiones admitidas, o elevaría la versión mínima de
  Python." → `_extract()` never extracts unfiltered; a member rejected by the `data`
  filter aborts the delivery (undelivered), and tests pin `../`, symlink, and oversized
  members.
- **S3** (source, verbatim, §1-Crítico 3): "Hay que extraer el proceso de sincronización y
  entrega a una operación reutilizable por run() y continue_task()." → a continuation that
  ends `completed` downloads the workspace, applies it to a local branch, and records the
  same state vocabulary as `run()` (delivered / undelivered).
- **S4** (source, verbatim, §1 "Otros problemas"): "_overlay() solo copia archivos
  existentes — Las eliminaciones hechas por el agente no llegan a la rama local." → the
  delivery manifest records files present in the workspace; deleted files are removed on
  the clone before `git add -A`.
- **S5** (source, verbatim, §1 "Otros problemas"): "task_id se genera con precisión de
  segundos — Colisiones al lanzar tareas simultáneas." → task ids are unique per store
  (uuid suffix), and a same-second launch no longer merges records; the branch name stays
  unique as a consequence.
- **S6** (source, verbatim, §1-Crítico 5): "Empezaría con memoria de solo lectura. Después
  incorporaría memory_save con validación, límites de tamaño, deduplicación y aprobación
  explícita o una política muy restrictiva." → memory defaults to read-only;
  `memory_save` needs an explicit opt-in flag.
- **S7** (source, verbatim, §1 "Otros problemas"): "Estado guardado al principio y al
  final del bucle — Recuperación incompleta si el proceso muere durante una llamada de
  función." → after every function round the record persists the current
  `interaction_id` (status `in_progress`), so `continue` and `status --live` work after a
  mid-loop death.
- **S8** (source, verbatim, §1 "Otros problemas"): "ask --stream imprime los deltas y
  después vuelve a imprimir la respuesta final — Puede duplicar la respuesta en
  terminal." → with `--stream` the final text is not printed a second time (a newline is
  added when the deltas did not end with one).
- **S9** (parent finding, 2026-11-09): `apply_to_branch` never commits and never resets
  the persistent clone, so task N's staged/uncommitted work pollutes task N+1's
  `diff --cached`/`status` (same root as §1-Crítico 2's residue). → after `git add -A`,
  the delivery is committed on its branch before the diff is reported; the next task
  starts clean.
- **S10** (source, verbatim, §1-Crítico 5, design half): memory writes need "validación,
  límites de tamaño". → `memory_save` content/title are length-capped in the adapter and
  an over-limit write is refused to the worker with a short error.
- **S11** Every behaviour change lands test-first, suite green (`python3 -m unittest
  discover -s tests`), one reviewable commit per work unit, Conventional Commits, version
  bumped per AGENTS.md §3 in the last commit of the unit (highest category present).
- **S12** Nothing is pushed, merged or applied to GitHub by the worker or the bridge;
  push/PR/merge stay the user's decisions (AGENTS.md §5, S9 of the gaps feature).

## Tasks

| ID | Linked | Route | Commit |
|---|---|---|---|
| T1 | S2 | **DONE** — fail-closed tar extraction: drop the unfiltered `except TypeError` fallback; reject traversal/symlink/oversized members; tests pin all three (RED first) | `fix(sync)` |
| T2 | S9, S4 | **DONE** — fresh delivery: commit on the branch after `git add -A`; deletions via workspace manifest; residue tests (previous-task files must not leak) | `fix(sync)` |
| T3 | S6, S10 | **DONE** — memory read-only by default (search/context); `memory_save` behind `--memory-save`; title/content caps + validation on the write path; tests | `feat(memory)` |
| T4 | S1 | **DONE** — no automatic remote-test execution: `run_tests` only behind `--run-tests`, env scrubbed on the opt-in path; report names that tests were not run | `feat(run)` |
| T5 | S3 | **DONE** — shared delivery operation used by `run()` and `continue_task()`; a completed continuation delivers like a run (delivered/undelivered, branch, tests flag respected) | `fix(run)` |
| T6 | S5 | **DONE** — unique task ids (uuid4 suffix) at both call sites; no record merge on collision; branch uniqueness; tests | `fix(run)` |
| T7 | S7 | **DONE** — persist `interaction_id` + `in_progress` after every function round; mid-loop death leaves a continuable record; tests | `fix(run)` |
| T8 | S8 | **DONE** — `ask --stream` prints the answer once; also send `task_parts(task, images)` on the stream path (parent finding: `--stream --image` dropped images) | `fix(ask)` |
| T9 | S11 | close: README + skill note the new flags/behaviours, CHANGELOG entry, bump, full suite | `chore(release)` |

## Log

- **L1** (user, verbatim, 2026-11-09): "si, en caso de que existan realmente ya que son
  señalamientos viejos que tenia anotados pero no se si con el pasar de los dias y los
  commits que hemos ido tirando se habaran ido solucionando por el camino como quien dice
  "sin querer"., en caso que aun exitan hagamso eso que propones"
- **L2** (verification, 2026-11-09) Explore agent + parent spot-checks against main@e2250db:
  C1 STILL (runner.py:487, sync.py:246-254), C2 STILL for same-repo residue
  (sync.py:217-222; mismatch half closed by F3), C3 STILL (runner.py:571-697 no
  delivery), C4 STILL (sync.py:298-302; floor >=3.10 makes the fallback reachable on
  unpatched 3.10.0-3.10.11), C5 STILL (engram_adapter.py:26-27, memory_enabled=True
  default, no gate), C6 STILL (runner.py:333,975; state.py:87-89 merges on collision),
  C7 STILL (sync.py:340-349 copy-only), C8 STILL (function_loop.py:96-104 new interaction
  id never persisted mid-loop), C10 STILL (runner.py:994 + cli.py:588-589). C9 FIXED
  (cancel wired, runner.py:904-925, google_client.py:270-282) — not re-done. Parent
  extras: delivery never committed (sync.py:222-236), `--stream --image` drops images
  (runner.py:989 vs 999), `tests_output` stored (runner.py:493) but never shown (cli.py
  559-562). Suite green pre-change: 265 tests OK.
- **L3** (slice boundary, 2026-11-09) 4 commits on feat/hardening since main:
  e5993b2 fix(sync) fail-closed extraction (T1), 33d7771 bump 0.24.1,
  3bf30b5 fix(sync) delivery commit + deletions (T2), 6c8c135 bump 0.24.2.
  Suite 278 OK at the boundary (215s). T1 + T2 DONE. Next unit T3.
- **L4** (slice boundary, 2026-11-09) T3 (memory opt-in) landed: 3295da3
  feat(memory) + 96bd265 bump 0.24.3. Suite 285 OK at the boundary (192s).
  T1+T2+T3 DONE. Slice now spans 706 authored lines across 6 commits
  (T1 extraction, T2 delivery, T3 memory) — past the ~400-line review slice,
  so this is one reviewable work unit (hardening) that will close in T9 with
  the docs + CHANGELOG + final bump, not a chained PR. Next: T4.
- **L5** (slice boundary, 2026-11-10) T4 (--run-tests opt-in + scrubbed env)
  = 206f56c + dbbd4f5 (0.25.0); T5 (_deliver shared by run/continue) =
  6c512e1 + e7a01a9 (0.25.1). Suite 294 OK. T1-T5 DONE. Remaining: T6 ids,
  T7 mid-loop state, T8 ask --stream, T9 close.
- **L6** (done, 2026-11-10) All nine tasks closed on feat/hardening,
  v0.24.1 → 0.25.5. Evidence per task (commit / suite):
  T1 e5993b2 · T2 3bf30b5 · T3 3295da3 · T4 206f56c · T5 6c512e1 ·
  T6 6c582db · T7 5ca7940 · T8 28cf074 · T9 docs + bump.
  Final suite: python3 -m unittest discover -s tests → Ran 305 tests, OK.
  C9 (remote cancel) was already fixed before this feature and was not
  re-done; the repo-mismatch half of C2 was already closed by F3.
  Deviation from AGENTS §3 noted to the user: each task was treated as its
  own work unit (one bump each) so native review can take a single finding
  alone, instead of one bump for the whole feature.
