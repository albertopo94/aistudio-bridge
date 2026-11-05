# Remote Worker — Plan de Producto

Producto standalone (decisión del usuario, opción A): orquestador local en Python
stdlib que delega tareas acotadas de código al agente administrado
`antigravity-preview-09-2026` de Google (Interactions API), conserva la memoria
local-first vía Engram y devuelve los cambios de código al git local del usuario
como branch con diff verificable.

## Índice

- `01-evidencia-poc.md` — todo lo que fue probado (Ruta A + Fase 2), con comandos,
  resultados, gotchas y costos en tokens. La base de hechos del plan.
- `02-diseno-arquitectura.md` — arquitectura del producto: componentes, flujo de
  datos, modelo de estado, seguridad, y lo que se reutiliza de agy-model-bridge
  (nada de código, solo decisiones de diseño).
- `03-plan-paso-a-paso.md` — roadmap de implementación por work units, con
  criterios de aceptación y evidencia requerida por unidad.

## Decisiones tomadas (con su origen)

1. **Producto standalone, no dentro de agy-model-bridge** (decisión del usuario).
   agy-model-bridge mantiene su promesa "pure stdlib, gateway stateless de
   modelos"; el remote worker es un workload stateful distinto (auth por API key,
   sesiones multi-turno, sandbox, diff de código). Pueden coexistir sin
   depender; si algún día se quiere superficie unificada, agy-model-bridge
   referenciará a este como servicio opcional.
2. **Sandbox = worker, no réplica del shell** (decisión con el usuario).
   Engram + Gentle-AI (ODD, skills, persona) viven en gentle-shell local.
   Al sandbox solo llega: código (source), contrato de worker (system_instruction)
   y tarea. La memoria se resuelve por callbacks ejecutados en el Mac.
3. **Worker nunca empuja a GitHub.** Git truth vive en el Mac. El retorno es:
   tar del workspace (Files API) → branch local → diff → el usuario decide el commit.
4. **Modelos permitidos:** `gemini-4-argon` (preferido por el usuario) y
   `gemini-3.8-flash` (default del agente, validado en free tier). Nada por debajo.
5. **Corre gratis en free tier (confirmado por estructura):** sin método de pago
   vinculado, es $0, incluyendo Argon. La tabla de precios que dice "Argon no
   disponible en free tier" fue refutada por prueba real (step 5 del PoC).
6. **Tres capas de instrucciones (aditivas, doc oficial custom-agents):**
   - `system_instruction` = contrato de worker, generado por el orquestador por tarea.
   - `AGENTS.md` del proyecto = convenciones, viaja dentro del repo montado (E1 probó
     que el harness lo lee de un repo montado).
   - `SKILL.md` en `.agents/skills/` = solo si la tarea lo necesita (montable inline).
7. **Presupuesto por tarea:** `agent_config.max_total_tokens` como seguro;
   `status: "incomplete"` al llegar al tope es un resultado esperado, no un error.

## Estado

- Investigación: completa (documento base: `investigacion-google-antigravity-engram-gentle-ai.md`).
- PoC: 7/7 pasos PASS (Ruta A 1-5 + Fase 2 E1-E3). Evidencia en `poc/`.
- Producto: **WU-0 a WU-7 completos (v0.1.0 → v0.9.0)**.
  - Setup interactivo con API key guardada en `~/.remote-worker/config.json` (0600), sin `.env` ni exports obligatorios.
  - Comandos disponibles: `setup` (con `--install-skill`, `--list-skill-backups`, `--restore-skill`), `run`, `ask` (con `--stream`), `status`, `continue`, `cancel`.
  - 81 tests unitarios contra fake HTTP server.
  - Siguiente sesión: ver la sección **Próxima sesión — self-test** abajo.
- Repo público: <https://github.com/albertopo94/aistudio-bridge>

## Próxima sesión — self-test (el producto corrigiendo al producto)

Meta: probar la delegación real (skill → CLI global → worker remoto) usando
tareas de este propio repo, **antes que cualquier otro fix** pendiente
(streaming para `run`, `ask` con repo, multi-agente, ADR 0001).

Condición dura: el worker monta el repo **desde GitHub**, no del worktree
local. Esta sección debe estar commiteada y pushed antes de delegar.

### Los 3 fixes del primer turno

| # | Fix | Aceptación |
|---|---|---|
| Q1 | Sección "Delegating from your agent" en `README.md`: cómo delegar desde un agente compatible (gentle-shell) usando la skill, con 1-2 prompts de ejemplo (código → `run`, no-código → `ask`). | El README documenta el flujo de delegación; los prompts nombran el trigger y el modo. |
| Q2 | El ejemplo "First run" deja de apuntar al repo juguete `worker-poc-toy` (era solo del PoC) y apunta a este repo: `--repo https://github.com/albertopo94/aistudio-bridge`. El bloque `Output:` se etiqueta como *sample output* (el worker no puede ejecutar la CLI desde el sandbox; un run local real puede reemplazarlo después). | El README no referencia `worker-poc-toy`; el output está etiquetado como sample. |
| Q3 | Alias `-v` de `--version` (hoy `remote-worker -v` falla con exit 2). `--version` y `-v` imprimen la versión y exit 0, fijados con test. | `remote-worker -v` → exit 0, imprime la versión. |

Notas de ejecución:
- Q2 es lo más barato y lo más demostrativo (self-delegation como ejemplo
  canónico). El orden Q1→Q2→Q3 es el pedido por el usuario.
- Q3 es seguro: el CLI no tiene modo `verbose`, así que `-v` no compite.
- Cada fix lleva sus tests; el bump de versión lo decide el worker según
  `AGENTS.md` §3 (se espera un solo bump para el work unit completo).

### Prompt de delegación (para la sesión fresca de gentle-shell)

> Delegate this to the remote worker: in the repo
> https://github.com/albertopo94/aistudio-bridge, implement the three fixes
> Q1, Q2 and Q3 documented under "Próxima sesión — self-test" in
> `plan-remote-worker/README.md`, in that order, before any other change.
> Read `AGENTS.md` first. The test suite
> (`python3 -m unittest discover -s tests`) must pass in the sandbox.

### Checklist de la prueba (qué valida el self-test)

1. gentle-shell descubre la skill `remote-worker` sin mención explícita del usuario.
2. `run` resuelve la key de `~/.remote-worker/config.json` sin `GEMINI_API_KEY` en el ambiente.
3. El diff vuelve en branch `worker/...` con `__pycache__`/`.git` purgados.
4. Los tests pasan al revisar la branch en el Mac.
5. El reporte trae status + tokens de budget; si `incomplete`, el comando exacto de `continue`.
6. El worker NO aplica ni mergea nada: el merge es decisión del usuario.

## Cobertura doc `agent-environment` (auditoría 2026-11-05)

Fuente: <https://aistudio.google.com/docs/agent-environment> (112k chars, lectura
completa). Veredicto: el flujo principal está cubierto (mount repository con
cap 500MB en subdir, tar por el endpoint nuevo no `:download`, reuso de
`environment_id`+`previous_interaction_id` en `continue`, state 0600,
lifecycle 15min/7días, key fuera del sandbox). Vacíos:

| # | Prioridad | Gap |
|---|---|---|
| G1 | media | `network` allowlist: hoy el sandbox tiene egress ilimitado (default). Choca con el espíritu de AGENTS.md §8. La doc da la solución exacta y desbloquea repos privados (PAT vía `transform: Authorization: Basic` o credential almacenada, sin que el secreto entre al sandbox). |
| G2 | baja | Nadie borra environments: `cancel` es local-only, los sandboxes viven 7d por TTL. Falta `Environments API` (list/get/delete) en el cliente + algún comando `clean`. |
| G3 | doc | README "Limits": el agente SOLO lee archivos de texto e imagen (binarios no soportados) y startup ~5s. Si un repo tiene binarios, el worker no puede leerlos. |
| G4 | feature (ya en TODO) | Repos locales >500MB: protocolo de upload resumable ahora documentado (`PUT /upload/...?uploadType=resumable`, chunks con `Content-Range`, `308` para reanudar, `extract=true` para tar.gz). Pasa de "investigar" a "implementar". |
| G5 | PoC (~15 min, post-self-test) | Medir el techo real del upload directo al sandbox ("fair usage limits" no está cuantificado en la doc; el matiz de 1.5 GB decide si GCS hace falta como fallback). Diseño en la sección TODO abajo. |

No aplican (no re-investigar): GCS source (sin GCS), inline source (marginal),
`env`/credentials en sandbox (contrario al diseño: nada de secretos adentro),
Hooks (los guardrails ya viven en el lado local: purge + `filter="data"` +
review humana del diff).

## Cobertura doc `managed-agents-quickstart` (auditoría 2026-11-05)

Fuente: <https://aistudio.google.com/docs/managed-agents-quickstart> (31k
chars, lectura completa). Cotejo de sus 5 apartados contra el producto:

| Apartado | Veredicto |
|---|---|
| `continue-conversation` | Cubierto: `continue_task` pasa ambos ejes (`previous_interaction_id` + `environment_id`). Las combinaciones mix-and-match restantes (nueva conversación en el mismo env / misma conversación en env nuevo) = G7, no necesarias hoy. |
| `automatic-context-compaction` | Nuevo dato: la API comprime sola el contexto a ~135k tokens en conversaciones largas (evita context rot). Cero acción (automático de Google); línea de doc = G6. La compacción opera sobre el *contexto*; `max_total_tokens` opera sobre el *uso total*: son dos cosas distintas, anotar en README "Limits". |
| `download-files` | Producto por delante de la doc: el quickstart sigue usando el endpoint legacy `/files/environment-{id}:download`; nosotros migramos al endpoint nuevo (`environments/{id}/files?alt=media&recursive=true`) en WU-3. |
| `save-managed-agent` | NO aplica por diseño: congelaría `system_instruction` + `base_environment` (sources fijos) + modelo en un registro. El contrato de worker es dinámico, generado por tarea (repo, tarea, tests, presupuesto). Agravante del PoC: `agents.create` lockea el modelo, y el producto necesita switching Argon/3.8-flash por tarea. En el radar si algún día se quiere un "worker estándar" de contrato fijo. |
| `invoke-managed-agent` | Consecuencia de 4. Dato: su ejemplo REST incluye el header `Api-Revision: 2026-05-20` (único de toda la doc); posible requisito del flujo de agents guardados. |

Discrepancias doc-vs-realidad confirmadas (no corregir el producto, anotar):
1. La doc dice "use `interaction.output_text`"; el PoC probó que el texto
   real está en `steps`/`model_output` — el producto lo lee de ahí.
2. Los ejemplos SDK de multi-turn sugieren timeout fijo de 5 min
   (`timeout: 300_000`); el producto usa polling (`run_until_complete`),
   correcto para tareas que superan ese bloque.

| # | Prioridad | Gap |
|---|---|---|
| G6 | doc | README "Limits": línea de context compaction a ~135k tokens (automática, lado Google) + aclaración compaction vs `max_total_tokens`. |
| G7 | baja | `--reuse-env`: mix-and-match de la doc (nueva conversación sobre el mismo sandbox) para no reinstalar dependencias en tareas encadenadas del mismo repo. |

## Cobertura doc `agent-environment` — pasada fina por subcapítulo (2026-11-05)

Misma fuente que la auditoría anterior (`agent-environment`), esta vez
subcapítulo por subcapítulo. Sin huecos de arquitectura; 2 gaps nuevos
menores + refinamientos de G1/G4:

| Subcapítulo | Veredicto |
|---|---|
| 3 formas de `environment` (`"remote"` / ID / config) | Cubiertas: `run` usa el objeto config, `ask` usa `"remote"`, `continue` usa el ID. |
| "Configure an environment" (instalar, guardar env, reusar) | Parcial: el instalar ya lo hace el worker por tarea; el patrón de env pre-configurado reutilizable = G7 confirmado. |
| "Mount from a source" (repository/gcs/inline, nunca `/`) | Cubierto: `mount_repo` apunta a `/workspace/<name>`; GCS/G4/G5 ya analizados; inline marginal. |
| "Hooks" (`.agents/hooks.json`, guardrails en sandbox) | Matiz: el riesgo principal que mitiga (worker con credenciales que hace push) no existe en nuestro diseño — el worker nunca recibe credenciales. Pasa de "no aplica" a "endurecimiento opcional de bajo valor". |
| "Private sources" (PAT vía transform, credential por ID) | Núcleo de G1, sin cambios. |
| "Pre-installed software" | NUEVO G8: Ubuntu con curl/wget/git/jq/ripgrep/gcloud; Python 3.12 con numpy/pandas/requests/google-genai; Node 22 con typescript/create-vite. `pip`/`npm install` en runtime persiste solo si se reusa el env (G7). |
| "Environment variables" (literal vs credential reference) | Correcto no usarlo. Dato valioso: con credential reference el sandbox recibe un placeholder y el proxy sustituye el secreto solo en salidas a dominios confiables — el worker podría llamar APIs externas sin ver el token. Bajo G1 como mecanismo conocido, fuera de scope hoy. |
| "Network configuration" (allowlist, `disabled`) | Refinamiento de G1: (a) el wildcard `*.example.com` NO matchea el dominio raíz — agregarlo aparte; (b) `network: "disabled"` existe → futuro flag `--no-network` directo. |
| "Credentials" (por ID, oauth2 auto-refresh, combo con transform) | Parte de G1. |
| "Refresh credentials" (env_id + nuevo `network`; FS se preserva) | Solo relevante si G1 usa transform con tokens cortos. Nota bajo G1. |
| "Environment lifecycle" | Cubierto (README Limits). |
| "Environments API" (list/get/delete) | G2. |
| "Manage files" (list/metadata/download/upload/resumable/409) | Cubierto (endpoint nuevo) + G4. Refinamiento: con `extract=true` el upload devuelve 409 si CUALQUIER archivo del tar ya existe en el target; G4 debe apuntar a target limpio o `overwrite=true`. |
| "Pricing & resources" (4 cores, 16 GB fijos) | Se pliega en G8 (misma línea de README). |
| "Limitations" | G3/G5. NUEVO G9: `environment_id` expirado/inválido devuelve 404; hoy `continue` a los 8 días devuelve error crudo de API. Falta: detectar el 404 y decir "sandbox expirado (7 días); la tarea no es reanudable". |

| # | Prioridad | Gap |
|---|---|---|
| G8 | doc | README: stack del sandbox (Ubuntu, Python 3.12, Node 22, tools pre-instalados) + 4 cores/16 GB fijos + `pip`/`npm install` en runtime. |
| G9 | bajo-medio | `continue` contra env expirado: 404 → mensaje amigable "sandbox expirado, no reanudable". |

## Cobertura doc `agent-hooks` (auditoría 2026-11-05)

Fuente: <https://aistudio.google.com/docs/agent-hooks> (49k chars, lectura
completa). Qué es, en una línea: **guardrails programables del sandbox** —
scripts o HTTP requests que se disparan antes/después de cada herramienta del
agente y pueden permitir, bloquear o actuar.

Veredicto global: **no aplica por diseño, ahora con razones técnicas** (antes
era descarte por juicio). Tres:

1. **Fail-open**: si el hook crashea, expira (timeout 30s default) o devuelve
   JSON no reconocido, el runtime trata todo como `allow` y la herramienta
   corre igual. El propio doc lo dice: hooks son "policy guidance", no
   enforcement. Nuestros guardrails viven local, donde no fallan abiertos:
   purge de `.git`/`__pycache__`, `filter="data"`, allowlist Engram y review
   humana del diff son enforcement real.
2. **No intercepta custom function calling ni MCP**: nuestras memory tools
   (resueltas localmente) están fuera de su alcance por construcción. El
   vector sensible del diseño se controla local, donde ya lo hacemos.
3. **Tamperable**: el propio agente (con write tools) puede modificar
   `.agents/hooks.json` en la misma sesión.

| Subcapítulo | Veredicto |
|---|---|
| Lifecycle events (solo `pre`/`post_tool_execution`) | Fail-open crítico: ver razón 1. `deny` educativo elegante pero nunca enforcement. |
| Config discovery (`.agents/hooks.json` desde cualquier source) | **G10**: un repo montado de terceros puede inyectar hooks → código ejecutado en el sandbox en la primera tool call. |
| Schema + matcher (regex RE2; `.*_file` no cubre `replace_file_content`/`list_dir`; globs de shell inválidos) | Gotchas de implementación si algún día se usa. Sin acción. |
| Command hooks / HTTP hooks (loopback bloqueado; requiere `network.allowlist`) | HTTP hook corre desde dentro del sandbox → dependencia de G1. |
| Failure semantics (crash/timeout/non-2xx → `allow`) | Confirmación textual de Google: hooks = guidance, no enforcement. |
| Use cases (privacy recovery con deny + multi-turn; audit telemetry) | Patrones inteligentes, no aplicados: nuestro control es contrato + review humana; loopback bloqueado impide incluso telemetría local. |
| Limitations (tool scope, config protection) | Razones 2 y 3 de arriba. |

| # | Prioridad | Gap |
|---|---|---|
| G10 | media (condicionada) | Repos montados de terceros pueden traer su propio `.agents/hooks.json` → código ejecutado en el sandbox en la primera tool call; hoy, con egress ilimitado (G1), incluye exfiltración del repo montado (un command hook puede leer `/workspace` y POSTearla afuera). Con solo repos propios el riesgo es ~nulo. Regla a documentar en README Security: delegar solo contra repos propios o confiables. Cuando G1 aterrice, este vector muere solo: el request no sale aunque el hook corra. |

Refinamiento de G1: esta fuente le da un argumento más fuerte — el allowlist
no es solo "el agente podría curlear", es la mitigación exacta del vector de
hooks maliciosos de repos de terceros (G10).

## Cobertura doc `antigravity-agent` (auditoría 2026-11-05)

Fuente: <https://aistudio.google.com/docs/antigravity-agent> (auditoría
delegada a subagente gentle-ai-explore; claims de código verificados en el
padre). Qué cubre en una línea: la guía oficial del agente — herramientas,
inputs, function calling, modelos, presupuesto, background/cancel, costos.

| Subcapítulo | Veredicto |
|---|---|
| Preamble (nombre del agente, llamadas básicas) | Cubierto (`runner.py` AGENT, `google_client.py` POST /v1beta/interactions). La doc sigue mostrando `output_text`; el producto lee `steps`/`model_output` (hallazgo PoC). |
| Supported tools (code_execution, google_search, url_context, environment, function, mcp_server) | Cubierto: el producto usa las por defecto; `function` = memory tools locales. |
| Multimodal input (text + image base64 solo; audio/video/doc no) | G11 (low, feature): input de imágenes no soportado hoy (solo texto). |
| Function calling (requires_action, pending calls, resumption stateful) | Cubierto: `function_loop.py` implementa el algoritmo de resolución exacto. |
| MCP servers | No aplica: la memoria es local-first (custom `function`), sin MCP remoto. |
| Model selection (4 modelos documentados) | Nuevo dato: la doc documenta también `gemini-3.6-flash` y `gemini-3.5-flash-lite`. El piso del producto (solo Argon + 3.8-flash) se mantiene por decisión del usuario, no por disponibilidad. |
| Background execution + cancellation endpoint | G12 (med): `cancel` hoy es SOLO local (`runner.py` cancel: "the Google interaction may still finish"). La doc da `POST /v1beta/interactions/{id}:cancel` — cancelación server-side que detiene el loop y ahorra quota free-tier. |
| Budget controls (max_total_tokens, status incomplete) | Cubierto (`planner.py` + manejo de `incomplete`). |
| Estimated costs (US$0.25–3.25 típicos; hasta ~US$5; 50-70% input cacheado) | Nuevo dato para README: baselines de costo por tarea. |
| Limitations (gen params 400, sin structured output, sin filesystem tool, audio/video/doc) | Ver "Delta de límites" abajo. |
| Triggers (cron, pausas, executions API) | No aplica: CLI local on-demand. |

Delta de límites vs README "Limits worth knowing" (candidatos a G6/G14):
1. `temperature`/`top_p`/`top_k`/`stop_sequences`/`max_output_tokens` → 400
   inmediato (el producto los omite, es un invariante a anotar).
2. Sin structured output (el agente no puede forzar schemas JSON).
3. `max_total_tokens` es **best-effort**: se evalúa entre pasos y puede
   exceder levemente el presupuesto antes de `incomplete`.
4. Tokens cacheados NO consumen `max_total_tokens`.
5. Cancelación server-side existe (G12).

| # | Prioridad | Gap |
|---|---|---|
| G11 | low (feature) | Input multimodal (image base64) para tareas con capturas/diagramas. |
| G12 | media | Cancelación server-side: `cancel` hoy es local-only; llamar `POST /v1beta/interactions/{id}:cancel` para detener el loop en Google y no gastar quota. |
| G13 | media (confiabilidad) | Background execution: hoy el cliente mantiene el HTTP abierto hasta 600s; la doc recomienda `background=True` + polling de `GET /v1beta/interactions/{id}` para tareas largas (evita dropouts de socket). |
| G14 | doc | README "Limits"/"Models": baselines de costo (US$0.25–3.25; ~US$5 máximo), 50-70% input cacheado, los 2 modelos adicionales existentes (el piso sigue siendo decisión del usuario). |

### TODO — PoC del upload resumable (~1.5 GB), post-self-test

Objetivo: medir el techo real de un upload directo al sandbox. Si 1.5 GB
pasa (entra y se queda), la vía GCS (2 GB documentados) es redundante para
nuestro rango y no entra al producto. Si falla, queda registrado el punto
de corte y GCS reentra como fallback documentado para arriba de ese.

Diseño (script en `poc/`, mismo patrón que e1-e3):

1. Crear un environment (interacción con `environment="remote"`).
2. Un único archivo de ~1.5 GB (`dd if=/dev/urandom of=big.bin bs=1m count=1536`
   — se prueba el techo de transporte/almacenamiento, no el contenido).
3. Init del upload resumable (`uploadType=resumable`), chunks de 10 MB.
4. Registrar: status final (200 o 4xx), punto de corte en bytes (si falla),
   tiempo total.
5. Borrar el environment (mide de paso el `DELETE` de G2).

Resultados esperados:
- (a) 200 OK → el upload directo cubre el rango que nos importa; README
  documenta el cap de `repository` 500 MB + el escape hatch resumable.
- (b) 4xx a cierto byte → ese es el techo efectivo de hoy; GCS queda como
  fallback documentado (2 GB) y el README pone el número medido, no uno
  inventado.

Costo: ~15 min, compute no facturado en preview, usa la API key existente.
Orden: después del self-test (primer turno de la siguiente sesión).

### TODO — G9: mensaje amigable para `continue` contra sandbox expirado

Hoy `continue` a los 7 días del TTL devuelve el 404 crudo de la API
(`Interactions API error 404: <body>`); el usuario no sabe que el sandbox
murió por diseño ni que la tarea es irrecuperable.

Diseño:

1. Reproducir primero (5 min, no inventar el 404): crear un environment,
   `DELETE`arlo, y llamar `create_interaction` con ese `environment_id`
   muerto. Registrar el body exacto que devuelve la API.
2. En `continue_task` (y el path de `ask` con env), capturar
   `InteractionsError` con `status == 404` y devolver:
   "the sandbox expired (7-day TTL); this task cannot be resumed —
   start a new run with the same repo".
3. Marcar la tarea `expired` en state para que `status` no la muestre
   como reanudable.
4. Test contra el fake HTTP server (404 → mensaje amigable + tarea
   marcada `expired`).

Orden: segundo turno de la siguiente sesión, después del self-test.
Independiente de G5 (su reproducción hace su propio `DELETE`).

## Referencias de memoria (Engram, proyecto aistudio-bridge)

- #2072 — evidencias PoC + Argon
- #2073 — diseño 3 capas de instrucciones
- #2074 — mecanismo de retorno del código (Files API)
- #2075 — alcance fase 2 + límite 500 MB con fuente
- #2077 — resultados fase 2 (E1-E3)
- #2078 — resumen de sesión completa
- #2079 — cierre de pendientes + decisión producto standalone

## Recursos

- Repo juguete (MANTENER, el usuario lo usa en otro trabajo):
  `github.com/albertopo94/worker-poc-toy`
- PoC y evidencia: `poc/` (scripts, logs/, e3-work/clone con branch worker/poc-e3)
- Clave: `poc/.env` (git-ignored, 600). El producto usará el mecanismo de
  credenciales del usuario, nunca este archivo.
