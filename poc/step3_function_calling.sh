#!/usr/bin/env bash
# Step 3: two-turn stateful function calling — validates the Engram
# callback strategy (A): the agent requests a function, THIS SCRIPT
# executes it locally (simulated Engram memory_search), and returns the
# result via previous_interaction_id.
# Success: status "requires_action" on turn 1 with a function_call for
# memory_search, then "completed" on turn 2 citing the fake memory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

# ---- Turn 1: ask a question that can only be answered via the tool ----
BODY1=$(python3 - <<'PY'
import json
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "input": "Before answering anything, you MUST call the memory_search tool with query 'capital de Argentina' and report exactly what the memory system returned. Do not answer from your own knowledge.",
    "environment": "remote",
    "tools": [
        {"type": "code_execution"},
        {
            "type": "function",
            "name": "memory_search",
            "description": "Search the local Engram memory store for past decisions and notes. Returns matching memories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in natural language"}
                },
                "required": ["query"]
            }
        }
    ],
}))
PY
)

R1="$LOG_DIR/step3_turn1.json"
echo "Turn 1: pidiendo al agente que use memory_search..."
CODE1=$(http_post 600 "$INTERACTIONS" "$BODY1" "$R1")
echo "HTTP $CODE1"
S1=$(json_field "$R1" "status")
IID=$(json_field "$R1" "id")
EID=$(json_field "$R1" "environment_id")
echo "status: $S1 | interaction_id: $IID | environment_id: $EID"
echo "uso turn 1: $(usage_summary "$R1")"
if [ "$CODE1" != "200" ]; then head -c 3000 "$R1"; echo; exit 1; fi

if [ "$S1" != "requires_action" ]; then
  echo "REVISAR: el agente no pidio la funcion (status=$S1)."
  echo "ultimo model_output: $(model_output "$R1")"
  echo "Puede haber respondido de memoria propia. Ver logs/step3_turn1.json."
  exit 1
fi

# ---- Local execution: simulate Engram memory_search ----
CALL_ID=$(python3 - "$R1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
done = {s.get("call_id") for s in d.get("steps", []) if s.get("type") == "function_result"}
for s in d.get("steps", []):
    if s.get("type") == "function_call" and s.get("name") == "memory_search" and s.get("id") not in done:
        print(s["id"]); sys.exit(0)
sys.exit(0)
PY
)
if [ -z "$CALL_ID" ]; then
  echo "REVISAR: no hay function_call pendiente para memory_search. Ver logs/step3_turn1.json"
  exit 1
fi
echo "El agente pidio: memory_search (call_id=$CALL_ID)"
echo "Ejecutando localmente (simulando Engram): query -> resultado fake"

FAKE_MEMORY='[{"title":"Decision de arquitectura","content":"La memoria debe vivir local-first en el Mac. El sandbox remoto es solo un cache de 7 dias.","type":"decision","relevance":0.97}]'

# ---- Turn 2: return the function result via continuity ----
BODY2=$(python3 - "$IID" "$EID" "$CALL_ID" "$FAKE_MEMORY" <<'PY'
import json, sys
iid, eid, call_id, fake = sys.argv[1:5]
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "previous_interaction_id": iid,
    "environment": eid,
    "input": [{
        "type": "function_result",
        "name": "memory_search",
        "call_id": call_id,
        # result debe ser string u objeto simple; un array JSON se envia como string.
        "result": fake,
    }],
}))
PY
)

R2="$LOG_DIR/step3_turn2.json"
echo "Turn 2: devolviendo el resultado local..."
CODE2=$(http_post 600 "$INTERACTIONS" "$BODY2" "$R2")
echo "HTTP $CODE2"
S2=$(json_field "$R2" "status")
OUT2=$(model_output "$R2")
echo "status: $S2"
echo "uso turn 2: $(usage_summary "$R2")"
echo "output_text: $OUT2"

if [ "$CODE2" = "200" ] && [ "$S2" = "completed" ] && echo "$OUT2" | grep -qi "local-first\|cache"; then
  echo "PASS: el ciclo completo de function calling stateful funciona."
  echo "interaction_id=$IID" > state.env
  echo "environment_id=$EID" >> state.env
else
  echo "REVISAR: ver logs/step3_turn2.json"
  exit 1
fi
