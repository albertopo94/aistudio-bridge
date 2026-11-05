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

No aplican (no re-investigar): GCS source (sin GCS), inline source (marginal),
`env`/credentials en sandbox (contrario al diseño: nada de secretos adentro),
Hooks (los guardrails ya viven en el lado local: purge + `filter="data"` +
review humana del diff).

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
