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
- Producto: **WU-0 a WU-4 completos (v0.1.0 → v0.5.0)**. El producto es
  usable: `remote-worker run "..." --repo <url>` delega la tarea, baja el
  resultado a una branch local, corre los tests del proyecto y reporta tokens.
  53 tests contra fake HTTP server; round trip real verificado dos veces
  contra `worker-poc-toy`. Sigue WU-5 (setup + docs + primera release).
- Repo público: <https://github.com/albertopo94/aistudio-bridge>

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
