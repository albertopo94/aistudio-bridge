# 03 — Plan Paso a Paso (roadmap por work units)

Implementación incremental de `02-diseno-arquitectura.md`. Cada work unit es
commiteable, testable y verificable por sí solo. El repo queda `git init`ado
con commits convenciales desde el primer unit (AGENTS.md §5).

**Convenciones de cada unit** (de AGENTS.md): test-first cuando hay test
declarado, evidencia citada en el cierre, bump de `VERSION`, y el estado del
repositorio siempre runnable.

## WU-0 — Base del repo (0.1.0)
- `git init` ya existe; primer commit: `AGENTS.md`, este `plan-remote-worker/`,
  `poc/README.md` (poc/ scripts quedan, son evidencia; `poc/.env` y `poc/logs/`
  ya están git-ignored).
- `VERSION` con `0.1.0`, `README.md` del producto (qué es, estado: pre-código),
  `pyproject.toml` mínimo (name, requires-python >=3.10, no deps) o solo
  estructura de package — decidir por la primera línea del código.
- `tests/` vacío con `__init__.py`; definir el comando canónico de test en
  `pyproject`/README (`python3 -m unittest discover -s tests`).
- **Aceptación:** `python3 -m unittest discover` corre (0 tests, exit 0);
  README explica el producto en una página; `git log` muestra el primer commit.

## WU-1 — Google Adapter: una interacción (0.2.0) ✅ COMPLETO

**Entregado** (`remote_worker/google_client.py`, 130 líneas, stdlib pura):
- `InteractionsClient(api_key, base_url, timeout)` — key solo en header
  `x-goog-api-key`, nunca en body ni en mensajes de error.
- `create_interaction(agent, input, environment, agent_config, tools,
  system_instruction, previous_interaction_id)` — envía solo los campos
  declarados; los `None` se omiten.
- `last_model_output(interaction)` — lee el **último** step `model_output`,
  no `output_text` (gotcha del PoC, con test que lo prueba).
- `usage(interaction)` — conteos de tokens.
- `InteractionsError(status, body)` — 429/4xx reportados con el cuerpo exacto,
  **sin retry automático** (test: exactamente 1 request).
- `InteractionsTimeoutError` — timeout claro, nunca cuelga.
- **Tests:** 5 tests contra fake HTTP server local (`tests/fake_interactions.py`)
  + 1 test base. Suite: 6/6 verde.
- **Smoke real verificado:** interacción completa contra la API, `status:
  completed`, 39.414 tokens, output correcto.

**Aceptación cumplida:** tests verdes; smoke real muestra status + tokens.

## WU-2 — Memory Adapter + bucle de function loop (0.3.0) ✅ COMPLETO

**Entregado:**
- `remote_worker/engram_adapter.py`:
  - `EngramMemory(project, binary, runner, timeout)` — ejecuta el CLI de
    Engram por subprocess; **fija el proyecto en cada llamada**.
  - `MemoryDispatcher.dispatch(call) -> str` — devuelve **JSON string**
    (nunca array: la API lo rechaza con 400). Descarta cualquier `project`
    que invente el worker.
  - Allowlist `ALLOWED_TOOLS = {memory_search, memory_save, memory_context}`;
    las operaciones admin (delete/prune/merge) son inalcanzables.
  - `EngramError` con el stderr real; `UnknownToolError` para tools fuera
    de la allowlist.
- `remote_worker/function_loop.py`:
  - `pending_calls(interaction)` — una call está pendiente solo si ningún
    `function_result` lleva su id (la API ecoa los pasos anteriores).
  - `run_until_complete(client, dispatcher, interaction, max_steps=10)` —
    responde los callbacks y reanuda con `previous_interaction_id`,
    `environment` y `agent`; `LoopLimitExceeded` acota el bucle y
    `NoPendingCallsError` corta un `requires_action` vacío.
- **Tests:** 13 nuevos (6 de adapter, 7 de loop). Suite total: 19/19 verde.
- **Smoke real verificado:** el escenario del E2 por código de producto —
  `requires_action` → dispatch local contra el Engram real → `completed`,
  respuesta `SOL-VERDE-77`, 5.286 tokens.

**Aceptación cumplida:** el scenario E2 se replica con código de producto.

## WU-3 — Workspace Sync ida y vuelta (0.4.0)
- `remote_worker/sync.py`:
  - `mount_repo(url, target) -> source dict` (repository source, 500 MB check
    con aviso).
  - `download_workspace(env_id, dir) -> tar path` (Files API, `?alt=media&
    recursive=true`).
  - `apply_to_branch(tar, repo_url, branch) -> (clone_dir, diff)`:
    clone fresco, extract a scratch, **purgar `.git`/`__pycache__`, chmod**,
    overlay, `git add -A`, `git diff --cached` (gotchas probados en E3).
  - `run_tests(clone_dir, cmd)` — test suite del proyecto en el Mac.
- Tests:
  - 1 test de `apply_to_branch` con un **tar sintético** que incluye un
    `.git` ajeno y un `__pycache__` con permisos raros → debe purgar y
    aplicar limpio (el bug exacto del PoC).
  - 1 test del payload del download (endpoint exacto, no el legacy
    `:download` deprecado).
- **Aceptación:** contra `worker-poc-toy`, el ciclo completo E3 corre por
  código de producto: tarea → diff limpio en branch `worker/*` → tests en el Mac.

## WU-4 — Task Planner + CLI + state (0.5.0)
- `remote_worker/planner.py`:
  - contrato de worker (template `system_instruction`),
  - selección de modelo (solo argon/3.8-flash; reject explícito de lo demás),
  - presupuesto por defecto + `max_total_tokens`.
- `remote_worker/cli.py` (entrypoint `remote-worker`):
  - `run <tarea> --repo <url> [--model] [--budget] [--branch-prefix worker/]`
  - `status`, `continue <task_id> --budget` (para `incomplete`), `cancel`
  - salida legible: estado, tokens usados, branch creada, diff summary,
    resultado de tests.
- `remote_worker/state.py`: estado JSON `0600` en
  `~/.remote-worker/<project>/state.json` (patrón de `~/.agy-bridge/`).
- Tests:
  - 1 test por comando (help, reject de modelo no permitido, parseo de
    presupuesto, estado del state file).
  - 1 test de que `incomplete` produce `continue` sugerido y no error.
- **Aceptación:** `remote-worker run "agregá multiply..." --repo
  https://github.com/albertopo94/worker-poc-toy` termina con branch local,
  diff y tests — el producto mínimo usable.

## WU-5 — Setup + docs + primera release (0.6.0)
- `remote-worker setup`: escribe config del usuario (key location, modelo
  default, presupuesto default) con backups timestamped (patrón de
  agy-model-bridge).
- `README.md` completo: instalación, first-run, ejemplo real con
  `worker-poc-toy`, límites (500 MB, RPD, 7 días), seguridad.
- `docs/adr/0001-standalone-product.md` (decisión opción A, contexto y
  consecuencias) — **recordatorio al humano** de que la ADR la firme él
  (AGENTS.md §9: no la escribo yo en silencio).
- CHANGELOG 0.1.0→0.6.0.
- **Aceptación:** un usuario nuevo con la key hace `setup` + `run` sin leer
  código; todos los ADRs/READMEs citan evidencia real.

## Fase 2 (después del 0.6.0, decisiones separadas)
- **Streaming SSE** (`?alt=sse`) para feedback en vivo.
- **Repos privados** (>500 MB incluidos): tar local → `PUT` resumable por
  chunks; el worker sigue sin credenciales.
- **Background + cancel** de Google para tareas largas.
- **Integración con gentle-shell** (frontera limpia: CLI invocable, no import).
- **`agents.create`** para agentes guardados (modelo locked) si el producto
  estabiliza un perfil de worker.

## Riesgos y mitigaciones (para tener a la vista)

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Preview cambia/retira el agente | Alto (es el motor) | Google Adapter aislado; el estado (memoria, git) vive en el Mac y no depende del agente |
| RPD free tier se agota | Medio | Presupuesto por tarea, reporte de tokens, `incomplete` como resultado |
| Sandbox muere a los 7 días | Bajo | El retorno se cierra por tarea; `continue` solo si el env sigue vivo |
| Engram cambia sus contratos | Medio | Memory Adapter usa el CLI público de Engram, no su SQLite interno |
| 429/400 intermitentes | Bajo | Backoff sin retry auto, cuerpo exacto al usuario |

## Criterio de "producto funcionando"

Un usuario con una API key puede:
1. `remote-worker setup` (una vez).
2. `remote-worker run "tarea acotada" --repo <url>` → ve el avance, el diff y
   los tests en el Mac.
3. Decide merge/discard con su git normal.
4. En todo momento ve tokens usados y estado de la tarea; `incomplete` no es
   un error.

Eso es el 0.6.0. Después viene la fase 2.
