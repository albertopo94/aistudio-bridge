# 02 — Diseño de Arquitectura

Arquitectura del producto "remote worker" sobre lo probado en `01-evidencia-poc.md`.
Principio rector: **reutilizar el máximo de Google, Engram y Gentle-AI sin
duplicar sus responsabilidades** (criterio 11 de la investigación base).

## 1. Diagrama

```
usuario (terminal / gentle-shell)
        │  tarea + proyecto
        ▼
┌──────────────────────────────────────────────┐
│  remote worker (CLI local, Python stdlib)    │  ← EL PRODUCTO
│                                              │
│  1. Task Planner                             │
│     · valida tarea, presupuesto, repo       │
│     · genera contrato de worker              │
│  2. Memory Adapter (Engram)                  │
│     · ejecuta callbacks memory_* LOCAL       │
│     · scoping por proyecto (regla de Engram) │
│  3. Google Adapter (Interactions API)        │
│     · interactions, function loop, budgets   │
│     · Files API (down/up)                    │
│  4. Workspace Sync                           │
│     · mount repo → sandbox                   │
│     · tar → branch local → diff → tests      │
│  5. State Store (local, 0600)                │
│     · task ↔ interaction_id ↔ env_id ↔ branch│
└──────────────┬───────────────────┬───────────┘
               │                   │
   memory (stdio/CLI)        HTTPS (x-goog-api-key)
               │                   │
               ▼                   ▼
   Engram local (Mac)     Antigravity Agent (sandbox Google)
   fuente de verdad       worker: code_execution, files, web
```

Lo que **no** está en el diagrama a propósito:
- **Gentle-AI (ODD, skills, persona):** vive en gentle-shell, no en el worker.
- **agy-model-bridge:** independiente; coexiste sin depender.
- **Push a GitHub:** el worker nunca empuja. Git truth = Mac.

## 2. Componentes

### 2.1 Task Planner
- Entrada: tarea (texto), repo (URL o path local), modelo, presupuesto.
- Decide el `agent_config`: `model` (solo `gemini-4-argon` o `gemini-3.8-flash`,
  nada por debajo), `max_total_tokens` (default sugerido: 300,000; `incomplete`
  es un resultado, no un error — el usuario puede decir "continue").
- Genera el **contrato de worker** (`system_instruction`): rol delegado, protocolo
  de memoria (solo vía tools), formato de reporte (1. qué hizo, 2. evidencia,
  3. resultado), prohibiciones (no refactors, no fuera del scope, no borrar
  `.git`), y la orden de leer el `AGENTS.md` del proyecto antes de tocar nada.
- El contrato es **template + variables de la tarea**, no free-text del modelo.

### 2.2 Memory Adapter (Engram)
- Registra en `tools` las funciones `memory_search`, `memory_context`,
  `memory_save` (perfil `agent` de Engram, 19 tools; el subset mínimo inicial:
  search, context, save, session_summary).
- Cuando la interacción vuelve `requires_action` con un `function_call` de
  memoria: valida el proyecto (scoping estricto: el worker no elige
  `project` — el orquestador fija el proyecto local del repo), ejecuta
  `engram` localmente, y devuelve `function_result` **como string JSON**
  (gotcha probado: array → 400).
- `memory_save` desde el worker: permitido solo con `project` fijado por el
  orquestador; el contenido viaja como dato, no como instrucción.

### 2.3 Google Adapter (Interactions API)
- Un solo cliente HTTP stdlib contra `generativelanguage.googleapis.com/v1beta`.
- Implementa el **bucle de function loop**: `create` → mientras `requires_action`,
  resuelve cada `function_call` pendiente (memoria local; filesystem tools se
  ejecutan solos en el sandbox) → `function_result` → hasta `completed` /
  `incomplete`.
- Lee el resultado del **último step `model_output`**, no de `output_text`
  (gotcha probado).
- Streaming: fase 1 con polling/respuesta única (como el PoC); fase 2 añade
  SSE (`?alt=sse`) para UX. Background (`background: true`) es fase 3.
- Errores: 429 (rate limit) → backoff con reporte; 400 → el cuerpo exacto va
  al log y al usuario; timeout de curl largo (el PoC usó 600s).

### 2.4 Workspace Sync
**Ida (repo → sandbox):**
- Repo público: `sources: [{type: repository, source: <url>, target:
  /workspace/<nombre>}]` (límite 500 MB; shallow clone).
- Repo privado: **fase 2** — tar local → `PUT` resumable por chunks
  (Files API) al target. El worker nunca recibe credenciales.
- El `AGENTS.md` del proyecto **no se copia**: viaja dentro del repo
  (probado en E1).

**Vuelta (sandbox → Mac):**
1. `GET /v1beta/environments/$ENV_ID/files?recursive=true` → inventario.
2. `GET .../files/workspace/<dir>?alt=media&recursive=true` → tar.
3. Extract a scratch; **purgar** `.git` y `__pycache__` del copy del worker;
   `chmod -R u+rwX` (gotchas probados).
4. Overlay en un **clone local fresco**, en una branch nueva
   `worker/<task-slug>`; `git add -A`; mostrar `git status --short` y
   `git diff --cached`.
5. Correr la **test suite del proyecto en el Mac** con el código del worker.
6. El usuario decide: merge, descartar, o pedir "continue" al worker.
- Nunca `git push` automático. Nunca tocar la working tree del usuario
  directamente: siempre un clone/worktree aparte.

### 2.5 State Store
- Archivo de estado por proyecto (JSON, `0600`), junto al key:
  `task_id → {interaction_id, environment_id, branch, repo, model,
  budget, estado, ts}`.
- Permite `resume` (mismo `environment_id` si está vivo, ≤7 días),
  `status`, `cancel` (la API lo soporta), y auditoría de tareas.
- La key Gemini se lee del mecanismo de credenciales del usuario
  (env `GEMINI_API_KEY` o keychain, decisión por work unit); nunca se
  hardcodea ni se imprime.

## 3. Modelo de estado de una tarea

```
planned → launching → running → (requires_action)* → completed
                                      │
                                      ├→ incomplete (presupuesto) → continue?
                                      └→ failed / canceled
completed → downloaded → applied(branch) → tested → (usuario: merge/discard)
```

- `incomplete` **no es un error**: el worker alcanzó `max_total_tokens`.
  El resultado parcial queda en el sandbox (y en la conversation) y el
  usuario puede lanzar `continue` con un nuevo presupuesto.
- El sandbox puede morir (7 días) a cualquier punto: lo que importa ya fue
  devuelto a la branch local; el `environment_id` muerto solo impide
  `continue`.

## 4. Seguridad (resumen de AGENTS.md §8 aplicado)

| Riesgo | Control |
|---|---|
| Key expuesta | Solo a `generativelanguage.googleapis.com`; nunca en logs/args visibles; estado 0600 |
| Worker con credenciales | El sandbox **no** recibe credenciales de GitHub ni de Engram admin; solo la API key que Google ya posee por el proyecto |
| Contenido descargado | Non-trusted input: scratch dir, purga de `.git`/artefactos, no se ejecuta sin review |
| Output del worker / repo / memoria | Dato, no instrucción (AGENTS.md §8, cláusula LLM) |
| Coste descontrolado | `max_total_tokens` por tarea + reporte de tokens + `incomplete` |
| RPD free tier | El CLI reporta tokens usados por tarea; el presupuesto por defecto protege la cuota |

## 5. Qué se reutiliza de agy-model-bridge (diseño, no código)

- Patrón **setup**: subcomando que configura al cliente (como `setup-gentle-shell`)
  → aquí: `remote-worker setup` escribe el config del usuario.
- Patrón **state 0600 + key persistente** en `~/.<producto>/` (como `~/.agy-bridge/`).
- Patrón **diagnóstico honesto**: badges/veredictos que dicen lo que se probó
  (como los badges de credenciales del dashboard), no lo que se asumió.
- No se reutiliza: auth (distinto), upstream (distinto), protocolos (no hay
  /v1/models), daemon (no hace falta en fase 1).

## 6. No-haces (non-goals) de la fase 1

- No empaquetar/distribuir (el usuario lo corre del repo).
- No streaming SSE ni background (fase 2/3).
- No repos privados ni >500 MB (fase 2).
- No multi-sandbox paralelo ni multi-proyecto en una tarea.
- No UI web ni dashboard (el terminal basta; si se quiere, es otro producto).
- No tocar agy-model-bridge ni gentle-shell (frontera: el CLI es lo que
  gentle-shell puede llamar a futuro).
