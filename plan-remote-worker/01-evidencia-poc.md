# 01 — Evidencia del PoC

Todo lo que fue **probado y observado** en esta investigación. Cada afirmación va con
su comando, resultado y evidencia (JSON en `poc/logs/`). Esto es lo que el plan de
producto puede dar por hecho. Lo que no está aquí es diseño o hipótesis.

Fecha de ejecución: 4 de noviembre de 2026. Todos los pasos corrieron con la key
free tier del usuario (sin método de pago → $0 por estructura).

## 0. Entorno base

| Dato | Valor |
|---|---|
| Máquina | macOS 15.7.4, **Intel x86_64** |
| Key | free tier, cuenta Pro, sin billing (→ $0) |
| Modelo default del agente | `gemini-3.8-flash` |
| Modelo probado por override | `gemini-4-argon` |
| Repo juguete | `github.com/albertopo94/worker-poc-toy` (público, **mantener**) |
| Engram local | v3.4.0, `engram` en `/usr/local/bin` |

## Ruta A — validar la key y las capacidades base

### Paso 1 — La key responde
- **Comando:** `POST /v1beta/models/gemini-3.5-flash-lite:generateContent` (10 tokens).
- **Resultado:** HTTP 200, `finishReason: STOP`, "OK". 5 tokens totales.
- **Conclusión:** la key vive y responde. (Usé el modelo solo como sonda barata de
  credencial; no es un modelo del producto.)

### Paso 2 — El agente corre con key free tier (la gran incógnita)
- **Comando:** `POST /v1beta/interactions` con `agent: antigravity-preview-09-2026`,
  `environment: "remote"`, tarea trivial (crear `hello.txt` + `ls -la`).
- **Resultado:** HTTP 200, `status: completed`. 9 pasos: 2 thought, 2
  code_execution, 1 function_call (write_to_file), 1 function_result, 1 model_output.
- **Tokens:** total 22,772 (input 21,186, cache 10,826, output 1,140, thought 446).
- **Conclusión:** **la key free tier funciona contra el agente.** Sandbox nuevo
  con `/.agents/`, `/.agent-templates-ro/`, `/app` (vacío) precargados.

### Paso 3 — Function calling stateful (el corazón de Engram)
- **Comando:** 2 turnos. Turn 1 con una custom function `memory_search`; el agente
  devuelve `status: requires_action` con un `function_call` pendiente. El script
  local ejecuta la "memoria" y devuelve `function_result` con `previous_interaction_id`.
- **Resultado:** Turn 1 → `requires_action` (pidió `memory_search`, 4,662 tokens).
  Turn 2 → `completed`, el agente citó de vuelta la memoria fake (4,968 tokens).
- **Conclusión:** **el ciclo worker → callback local → resultado → worker funciona.**
  Esto es exactamente lo que Engram necesita.
- **Gotcha crítico:** `function_result.result` **no puede ser un array JSON** —
  Google lo interpreta como lista de content-parts y devuelve `400`. Debe ser
  **string** (JSON serializado) u objeto simple. Para Engram: serializar los
  resultados como string JSON.

### Paso 4 — Persistencia de archivos
- **Comando:** Turn A escribe un `marker.txt` con un valor único en un sandbox nuevo.
  Turn B, **nueva conversación** (sin `previous_interaction_id`), mismo
  `environment_id`, pide leerlo.
- **Resultado:** Turn B devolvió el marker exacto. 35,150 tokens (21,482 cacheados).
- **Conclusión:** los archivos persisten entre conversaciones dentro del mismo
  `environment_id`.

### Paso 5 — Modelo Argon vía `agent_config`
- **Comando:** `agent_config: {"type":"antigravity","model":"gemini-4-argon","max_total_tokens":100000}`.
- **Resultado:** HTTP 200, `status: completed`, 11,302 tokens. La respuesta **ecoó
  `agent_config.model: gemini-4-argon`** → no hubo fallback silencioso; corrió Argon.
- **Conclusión:** **Argon corre con key free tier**, lo cual contradice la tabla de
  precios que lo marca "Not available" en free tier. Confirmado $0 por estructura
  (sin método de pago). `max_total_tokens` funciona como presupuesto por interacción.

## Fase 2 — calibrar la arquitectura de la ruta C

### E1 — ¿El harness lee el AGENTS.md de un repo montado?
- **Comando:** `sources: [{type: repository, source: <repo juguete>, target:
  /workspace/worker-poc-toy}]` + `system_instruction` de contrato. Pregunta al
  worker: "¿cuál es tu password?" (el `AGENTS.md` del repo lo declara: `BLUE-MOON-42`).
- **Resultado:** el worker respondió `BLUE-MOON-42` **citando la línea exacta** del
  `AGENTS.md`. 34,132 tokens.
- **Conclusión:** **el harness lee el AGENTS.md de un repo montado directamente.**
  Caso ideal: las convenciones del proyecto viajan con el código, sin que
  gentle-shell/copie nada a `.agents/`.

### E2 — Memoria contra el Engram REAL del Mac
- **Comando:** contrato de worker por `system_instruction` + tool `memory_search`.
  El worker pidió `memory_search("marcador de calibracion de la memoria")`; el
  script ejecutó `engram search` **local** contra el proyecto real y devolvió el
  resultado. El worker tenía que responder con el marcador (`SOL-VERDE-77`, obs #2076).
- **Resultado:** el worker respondió `SOL-VERDE-77` citando la observación real.
  5,377 tokens (turno 2).
- **Conclusión:** **el ciclo completo de la ruta C está probado:** worker remoto →
  callback → Engram real del Mac → worker, con contrato por `system_instruction`.

### E3 — Loop ida y vuelta del código
- **Comando:** worker con contrato; tarea: "agregá `multiply(a,b)` a `main.py` + 2
  tests, no toques nada más, corré los tests". Luego descarga
  `GET /v1beta/environments/$ENV_ID/files/workspace?alt=media&recursive=true` (tar).
- **Resultado:** worker modificó `main.py` + `tests/test_main.py` (97,774 tokens),
  tests pasaron en el sandbox. Tar de 60,928 bytes. Aplicado en clon local en la
  branch `worker/poc-e3` → **diff limpio de solo los 2 archivos** → **tests pasan en el Mac**.
- **Conclusión:** el criterio "Código recuperable" y la pregunta #36 están cubiertos:
  el código vuelve de forma verificable, sin pisar nada, git truth en el Mac.
- **Gotchas de implementación (importantes para el producto):**
  1. El tar trae el `.git` del worker y `__pycache__` con **archivos root-owned de
     permisos rotos** → extraer todo, purgar `.git`/`__pycache__` del copy del
     worker, `chmod -R u+rwX`, y **entonces** overlay. El `--exclude` de macOS tar
     **no** respeta contenidos; falló en el primer intento.
  2. El clonado `repository` es **shallow** en el sandbox (`.git/shallow`).

## Cifras de costo (tokens) para dimensionar

| Tarea | Tokens |
|---|---|
| Tarea trivial (crear archivo + ls) | ~23k |
| Read-back de marker | ~35k (21k cacheados) |
| Montar repo + pregunta de password | ~34k |
| Memoria (turno de respuesta) | ~5k |
| **Tarea real de modificar código** | **~98k** |

En free tier no cuesta dinero, pero consume **RPD** (requests per day) del proyecto.
La doc de Google estima 100k–3M por interacción agéntica; nuestras tareas acotadas
quedan en la franja baja (~23k–98k). El RPD de free tier limita cuántas tareas
reales por día — el producto debe respetar `max_total_tokens` y reportar `incomplete`.

## Mecanismo de retorno (Files API) — endpoints verificados en doc

| Operación | Endpoint |
|---|---|
| Listar (recursivo) | `GET /v1beta/environments/$ENV_ID/files?recursive=true` |
| Descargar archivo | `GET .../files/<path>?alt=media` |
| Descargar dir como tar | `GET .../files/<dir>?alt=media&recursive=true` |
| Subir archivo | `PUT /upload/v1beta/environments/$ENV_ID/files/<path>` (resumable por chunks) |
| Subir tar con extracción | `PUT .../files/<dir>/?extract=true` |

**Lifecycle del sandbox:** idle 15 min → auto-snapshot y stop; 7 días sin actividad
→ borrado. El ciclo de retorno se cierra **por tarea**, no se estaciona.

**Límites de mount:** `repository` 500 MB · `gcs` 2 GB · `inline` 1 MB/archivo,
2 MB total (fuente: docs/agent-environment, sección "Mount from a source").

## Resumen de lo que el producto puede dar por hecho

1. La key free tier ejecuta el agente (3.8 Flash y Argon). $0 por estructura.
2. Function calling stateful de 2 turnos es el mecanismo de Engram (result como string).
3. Archivos persisten por `environment_id` (TTL 7 días).
4. El harness lee el `AGENTS.md` de un repo montado (sin copias).
5. El worker respeta un contrato por `system_instruction`.
6. El código vuelve como tar → diff limpio en branch local → tests en el Mac.
7. `max_total_tokens` limita presupuesto; `incomplete` es un resultado válido.
