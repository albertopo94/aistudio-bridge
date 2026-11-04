# PoC — Ruta A: validar la key de Gemini API contra Antigravity Agent

Investigación descarte-able. No es producto. Todo corre contra endpoints
oficiales de Google; ninguna clave se imprime.

## Estado

| Paso | Qué valida | Costo esperado |
|------|-----------|----------------|
| 1 | La key vive y bajo qué tier responde | 1 micro-llamada gratis |
| 2 | El agente preview acepta la key (free tier o no) | 1 interacción de agente, tokens gratis en free tier; sandbox no se factura en preview |
| 3 | Function calling stateful de 2 turnos (la estrategia A de Engram) | 2 interacciones |
| 4 | Persistencia de archivos entre conversaciones en el mismo environment_id | 2 interacciones |

Datos: solo contenido de juguete (hola.txt, un marker aleatorio, una memoria
fake). En free tier Google usa el contenido para entrenar: nada real.

## Antes de correr (una sola vez)

1. Copiá tu API key desde https://aistudio.google.com/api-keys
2. Colocala sin que quede en el historial del shell (no la escribas en el comando):

```bash
umask 077; read -s -p "Pega la API key: " k && printf '%s' "$k" > /Users/albertmac11/Reguero/Proyectos/aistudio-bridge/poc/.env
```

(El `.env` queda con permisos 600 y está en el `.gitignore` del directorio.)

## Ejecución (paso a paso, ver entre medio)

```bash
cd /Users/albertmac11/Reguero/Proyectos/aistudio-bridge/poc
./step1_key_check.sh
./step2_agent_first.sh
./step3_function_calling.sh
./step4_persistence.sh
```

Cada paso guarda su evidencia en `logs/` y muestra PASS/FAIL con el estado
devuelto por Google.

## Criterios de éxito totales

1. Step 2 responde `status: completed` → la key funciona contra el agente.
2. Step 3 completa el ciclo `requires_action` → `function_result` → `completed`
   → la estrategia A de Engram (callbacks ejecutados localmente) es viable.
3. Step 4 recupera el marker exacto → los archivos sobreviven a conversaciones
   nuevas dentro del mismo environment_id.

## Limpieza (opcional)

Las interacciones almacenadas se retienen 1 día en free tier (según doc);
el environment se borra solo a los 7 días de inactividad. No queda nada
persistente local salvo `logs/` y `state.env`, ambos ignorados por git.
