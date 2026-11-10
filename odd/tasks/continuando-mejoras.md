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
| T1 | P0-1 | snapshot identity (base_commit/base_sha) + apply contra ese punto | — |
| T2 | P0-3 | corregir afirmación/protección de `--run-tests` (docs + mensaje) | — |
| T3 | DOC-1 | sincronizar CHANGELOG hasta v0.25.7 | — |
| T4 | P1-1 | completar o desactivar la ruta GCS | — |
| T5 | P0-2 | aislar tareas concurrentes | — |
| T6 | P1-2 | retorno incremental de workspaces grandes | — |
| T7 | D-1 | idempotencia de llamadas a funciones | — |
| T8 | D-2 | reconciliación de interacciones completadas | — |

## Log

L1 (2026-11-10): el usuario preguntó "en que nos quedamos ? que tocaba hacer en
esta sesion?", y aceptó ("si") arrancar por P0-1 con feature doc ODD. Backlog
original en `continuando con mejoras.md` (revisión post-hardening v0.25.7).