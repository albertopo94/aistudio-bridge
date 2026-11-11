# Continuando con mejoras — post-hardening review (v0.25.7)

Cierre de la base para que aistudio-bridge sea un runtime de ejecución remota
seguro y concurrente (camino hacia un servidor MCP propio en Gentle Shell).
Cinco condiciones técnicas (P0/P1) de la revisión post-hardening, más dos
requisitos de diseño y un ajuste documental. Rama: `feat/continuando-mejoras`.

## Specs

### P0-1 · Fijar el commit base y la identidad de cada snapshot
El problema: `run()` toma una instantánea del proyecto (para `--local-repo`,
`archive_repo()` empaqueta el working tree; para `repo_url`, el sandbox monta
la rama remota) pero NO registra el punto exacto que Antigravity recibió.
`apply_to_branch()` entrega sobre `_remote_base(clone)` = la ref remota ACTUAL.
Si la rama original avanza (commit B) mientras el agente trabaja sobre A, la
entrega se construye sobre B pero superpone el contenido completo de A, y
`_drop_removed()` puede borrar archivos que B agregó.

- Toda tarea con repositorio (`run` y `continue`) debe persistir en el registro
  un `base_commit` (y para `--local-repo`, un `base_sha` del árbol enviado) que
  fije el punto de partida exacto.
- `apply_to_branch()` debe construir la entrega contra ESE punto, no contra la
  ref remota del momento, y detectar conflicto cuando la rama original avanzó
  (reportando el hecho en vez de silenciar archivos ajenos).

### P0-2 · Aislar las tareas concurrentes
El runner usa `~/.remote-worker/<project>/work/clone` compartido, un solo
`TaskStore` JSON y un `MemoryDispatcher` con `allow_save` mutable. Dos tareas
simultáneas pueden corromper el clon, perder actualizaciones de `state.json` o
cambiar el permiso de memoria de la otra.

- Un workspace de entrega independiente por tarea.
- Escrituras de estado seguras bajo concurrencia (no solo atómicas).
- Permisos de memoria inmutables y específicos de cada tarea.
- Control explícito de los trabajos activos y reconciliación sin depender del
  estado en memoria de un único proceso.

### P0-3 · Corregir la afirmación y la protección de `--run-tests`
`test_env()` sustituye HOME y limpia variables por allowlist, pero el proceso
corre con el MISMO usuario del Mac: no aísla el sistema de archivos (rutas
absolutas como `/Users/<user>/.ssh/` siguen legibles) y no restringe red. No se
debe describir esa ejecución como un sandbox sin acceso a secretos.

- `--run-tests` mantiene su semántica (desactivado por defecto, opt-in explícito),
  pero el mensaje/ayuda no debe afirmar que aísla el filesystem ni la red.
- Documentar que la ejecución de tests del entregado no es un sandbox: corre con
  el usuario local, con entorno reducido y sin restricción de red.

### P1-1 · Completar o desactivar la ruta GCS
`run --gcs-source` construye la entrada (`gs://...`), pero la entrega llama a
`apply_to_branch()` que espera `git clone` de una URL. Una URI `gs://` no es un
repo Git, así que la vía GCS acaba en `undelivered` sin flujo de retorno completo.

- O completar el retorno para la vía GCS, o desactivarla explícitamente
  (`--gcs-source` rechaza con mensaje claro) hasta tener el flujo completo.
- La ruta GCS se mantiene marcada `not validated live` en `routes.py`.

### P1-2 · Resolver la descarga incremental de workspaces grandes
El retorno completo por TAR tiene límites medidos (1 GiB ok, 2 GiB rechazado,
TAR de 1 y 3,6 GiB fallan a ~122 s). `FilesClient.download_to()` lee toda la
respuesta en memoria antes de escribir a disco.

- `fetch --path` ya da recuperación manual por archivo; falta integrar un
  retorno incremental o por archivos cuando el TAR completo no sea viable.
- No depender de un TAR completo como única vía de retorno.

### D-1 · Idempotencia de llamadas a funciones
Una llamada local (p. ej. `memory_save`) puede ejecutarse y persistir antes de
que su resultado llegue a Google; si el proceso muere en esa ventana, al
continuar la llamada puede ejecutarse otra vez. Sin garantía de exactly-once.

### D-2 · Reconciliación de interacciones completadas tras interrupciones
`continue_task()` consulta la interacción, pero si ya está completada no
recupera su resultado directamente: crea otra interacción con `continue`. Se
debe reconciliar: ejecutándose → seguir; completada → recuperar y `_deliver()`;
fallida → estado terminal; llamada local pendiente → idempotencia.

### DOC-1 · Sincronizar CHANGELOG con el código
El código está en v0.25.7 pero el CHANGELOG termina en v0.25.5. Mantener el
historial de cambios sincronizado con las versiones publicadas.

## Tasks

| ID | Spec | Ruta | Commit |
|----|------|------|--------|
| T1 | P0-1 | snapshot identity (base_commit/base_sha) + apply contra ese punto | 49e7891 |
| T2 | P0-3 | corregir afirmación/protección de `--run-tests` (docs + mensaje) | 4aa6bb1 |
| T3 | DOC-1 | sincronizar CHANGELOG hasta v0.25.7 | c16fc85 |
| T4 | P1-1 | completar o desactivar la ruta GCS | 9c895ca |
| T5 | P0-2 | aislar tareas concurrentes | 94e950e |
| T6 | P1-2 | retorno incremental de workspaces grandes | 5ee30dd |
| T7 | D-1 | idempotencia de llamadas a funciones | f7e86c7 |
| T8 | D-2 | reconciliación de interacciones completadas | — |

## Log

L1 (2026-11-10): el usuario preguntó "en que nos quedamos ? que tocaba hacer en
esta sesion?", y aceptó ("si") arrancar por P0-1 con feature doc ODD. Backlog
original en `continuando con mejoras.md` (revisión post-hardening v0.25.7).

L2 (2026-11-10): T1 cerrado en 49e7891 (feat(sync)). Suite 316 OK. El feature doc
va en 1595d08 (docs). T1 implementa: `archive_repo` devuelve `base_commit`; `run`
y `continue_task` lo registran (local-repo = HEAD empaquetado, repo remoto =
`_remote_head`); `apply_to_branch` acepta `base_commit`, ancla la entrega en ese
punto y reporta `base_moved` cuando la rama avanzó. Falta por decidir en P0-1:
reportar `base_moved` en el CLI/status (hoy solo queda en el resultado y el store).

L3 (2026-11-10): T2 cerrado en 4aa6bb1 (docs(run-tests)), T3 en c16fc85
(docs(changelog)), T4 en 9c895ca (feat(gcs)). Suite 309 OK. T2 corrige la
afirmación de `--run-tests` (el entorno reducido NO es un sandbox: corre con el
mismo usuario y con red). T3 agrega 0.25.6 y 0.25.7 al CHANGELOG. T4 desactiva
`--gcs-source` (rechaza en lanzamiento; la tabla de rutas lo marca
`STATUS_DISABLED`) hasta que tenga retorno git.

L4 (2026-11-10): T5 cerrado en 94e950e (feat(sync)). Suite 314 OK. Test-first:
5 tests nuevos en tests/test_concurrency_isolation.py (todos observados RED
antes). Implementa las cuatro condiciones de P0-2: (1) cada entrega aplica en
su propio clon `work/<task-id>/clone` (antes: `work/clone` compartido, race de
re-clone/reset/_drop_removed); (2) `TaskStore.record` corre su read-modify-write
bajo `flock` exclusivo sobre un lockfile lateral — el test multiproceso (4
procesos escribiendo el mismo state.json) mostraba pérdida de records y un
FileNotFoundError en el rename .tmp; (3) `MemoryDispatcher.allow_save` es
inmutable (property de solo lectura) y el runner arma un dispatcher por tarea
(`_task_dispatcher`), así el `--memory-save` de una tarea no contamina a otra
(test de no-bleed: dos tasks con flags opuestos en el MISMO runner); (4)
reconciliación de trabajos activos: el store durablo, escrito bajo lock, es la
única fuente de verdad y ninguna flag en memoria se comparte entre tareas.
Afectados: README (rutas de clon), tests de entrega existente (ruta por
Tarea). Riesgo: item 4 (concurrencia).

L5 (2026-11-10): boundary de slice: la rama acumula ~1.4k líneas desde main
(8 commits, T1–T5). Entrega: rama local feature; push/PR sigue siendo decisión
del usuario (decisión del cierre anterior). Quedan T6–T8.

L6 (2026-11-10): cierre de sesión. El usuario pidió cerrar y reiniciar: rama
`feat/continuando-mejoras` mergueada a `main` (fast-forward cc5789c→d594056,
v0.26.0) y pusheada a GitHub. Working tree limpio.

L7 (2026-11-10, sesión 2): arrancó rama `feat/retorno-recuperacion` desde
main. Cerrado el follow-up de P0-1 (L2): `base_moved` se reporta ahora en el
CLI — `feat(cli)` f1497d4, v0.26.1→0.27.0. Test-first: `test_completed_warns_when_base_branch_moved`
y `test_status_reports_base_moved_when_present` en tests/test_cli.py (ambos
observados RED). `_report_run` imprime una advertencia cuando `base_moved` es
True y `_report_status` imprime la clave cuando está registrada. Suite 316 OK
(antes del bump, sin cambio de comportamiento adicional). Resto de la sesión:
T6, T7, T8.

L8 (2026-11-11, sesión 2): T6 cerrado en 5ee30dd (feat(sync)), v0.28.0.
Test-first: tests/test_workspace_fallback.py (4 reglas, RED observado) y
`test_download_streams_the_body_in_bounded_chunks` (RED observado).
Implementa P1-2 en dos piezas: (1) `FilesClient.download_to` ahora
streama el cuerpo a disco en chunks de 1 MiB (`_send_to_file` en
google_client.py) en vez de bufferizar toda la respuesta en memoria —
la semilla del fallo medido (2 GiB rechazado; TARs grandes mueren a
~122 s) — conservando la semántica de errores de `_send` exactamente,
por lo que la entrega y `fetch --path` se benefician sin cambiar
callers. (2) `_deliver` ahora tiene un fallback: cuando la descarga del
TAR completo falla (InteractionsError/timeout), `_rebuild_workspace_tar`
reconstruye el mismo layout (`<repo_name>/...`) archivo por archivo vía
la lista recursiva + una descarga por archivo (el mismo transporte que
`fetch --path`), saltando `.git` y build artifacts (la entrega los
purga igual). El resultado y el CLI dicen `per-file` cuando ocurre el
swap; si AMBAS vías fallan, la tarea queda `undelivered` nombrando
AMBOS fallos. Restricción honesta: el fallback solo se dispara ante un
fallo de la descarga del árbol (el caso medido), no ante un tar corrupto
200. Suite 321 OK.

L8b (2026-11-11): corrección documental: la entrada del changelog del
follow-up P0-1 (f1497d4) se etiquetó 0.26.1 pero VERSION era 0.27.0
desde ese commit; corregida a 0.27.0 en 5ee30dd. No hubo versión
0.26.1 publicada.

L8c (2026-11-11): BOUNDARY DE SLICE: la rama acumula ~519 líneas desde
main (f1497d4, e57c371, 5ee30dd + docs). Se excedió el slice de ~400
líneas; T7 arranca un slice nuevo. Estrategia de entrega: rama feature
local; push/PR sigue siendo decisión del usuario. Riesgo T6: item 5
(entorno/transporte de la entrega) y 6 (sin test de red real: el
fallback se prueba contra un fake de los endpoints, no contra el
servicio vivo).

Restante: T7 (D-1), T8 (D-2).

L9 (2026-11-11, sesión 2): T7 cerrado en f7e86c7 (feat(loop)), v0.29.0.
Test-first: tests/test_idempotent_calls.py (4 reglas, RED observado).
Implementa D-1: cada llamada local resuelta se persiste en el registro de
tarea bajo `local_calls` -> `<interaction_id>` -> `<call_id>` inmediatamente
despues del efecto secundario, y el loop (`run_until_complete` /
`wait_until_done`) la consufta antes de despachar y la escribe despues: si
el proceso muere en la ventana entre el efecto y la ronda que lo reporta a
Google, un `continue` que re-lee la misma interaccion replay la respuesta
guardada en vez de re-ejecutar la llamada (exactly-once por interaccion/
llamada). Una llamada que lanza no se cachea: un reintento fresco la
re-ejecuta. Key por (interaction_id, call_id): el mismo call_id en OTRA
interaccion es una llamada nueva y se ejecuta. `call_cache` es un parametro
opcional (default None -> comportamiento previo intacto, regla 4). Backed
por TaskStore.record bajo lock (P0-2). Suite 326 OK. Riesgo: item 6 (no hay
test de red real; idempotencia probada contra fakes del cliente y del store).

PENDIENTE PARA LA SIGUIENTE SESIÓN (en este orden recomendado):
- [x] T6 · P1-2 — retorno incremental de workspaces grandes: cerrado en
  5ee30dd (L8).
- [x] T7 · D-1 — idempotencia de llamadas a funciones locales: cerrado en
  f7e86c7 (L9).
- T8 · D-2 — reconciliar interacciones ya completadas en `continue_task()`:
  hoy, si la interacción ya terminó, se crea otra interacción nueva con
  `continue`; debe recuperar el resultado y entregarlo (`_deliver()`) en vez
de re-pagar.
- [x] Follow-up P0-1 (L2): reportar `base_moved` en el CLI/status — cerrado
  en f1497d4 (feat(cli), sesión 2, rama feat/retorno-recuperacion, L7).
- La rama feature quedó en main; el branch local `feat/continuando-mejoras`
  se mantiene (igual que `feat/hardening`), no se borró.