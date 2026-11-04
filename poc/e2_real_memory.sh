#!/usr/bin/env bash
# E2: system_instruction as worker contract + memory_search custom function
# resolved against the REAL local Engram (via `engram search` on this Mac).
# The agent must NOT know the marker; it can only get it by calling the tool,
# which this script executes locally, exactly as gentle-shell would.
# Success: worker quotes SOL-VERDE-77 (the real Engram content).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

# ---- Turn 1 ----
BODY1=$(python3 - <<'PY'
import json
si = (
    "You are a delegated code worker, not an orchestrator. "
    "You execute one bounded task in the sandbox and report back. "
    "Memory protocol: for anything about past decisions, project context, or "
    "calibration markers, you MUST call the memory_search tool and use only "
    "what it returns. Never invent or guess memory contents from your own "
    "knowledge. If memory_search returns nothing, say so explicitly. "
    "Report format: (1) what you did, (2) evidence, (3) result."
)
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "agent_config": {"type": "antigravity", "model": "gemini-3.8-flash", "max_total_tokens": 150000},
    "system_instruction": si,
    "input": "What is the calibration marker for memory? You must answer using the memory_search tool with query 'marcador de calibracion de la memoria'.",
    "environment": "remote",
    "tools": [
        {"type": "code_execution"},
        {
            "type": "function",
            "name": "memory_search",
            "description": "Search the user's persistent project memory (Engram) for past decisions, discoveries and calibration data. Returns matching memories as JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query"},
                },
                "required": ["query"],
            },
        },
    ],
}))
PY
)

R1="$LOG_DIR/e2_turn1.json"
echo "E2 turn 1: worker con contrato + tool memory_search..."
CODE1=$(http_post 600 "$INTERACTIONS" "$BODY1" "$R1")
echo "HTTP $CODE1"
S1=$(json_field "$R1" "status")
IID=$(json_field "$R1" "id")
EID=$(json_field "$R1" "environment_id")
echo "status: $S1 | interaction: $IID | env: $EID"
[ "$CODE1" = "200" ] || { head -c 2000 "$R1"; exit 1; }

if [ "$S1" != "requires_action" ]; then
  echo "E2 REVISAR: el worker no pidio memory_search (status=$S1)."
  model_output "$R1"
  exit 1
fi

CALL_ID=$(python3 - "$R1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
done = {s.get("call_id") for s in d.get("steps", []) if s.get("type") == "function_result"}
for s in d.get("steps", []):
    if s.get("type") == "function_call" and s.get("name") == "memory_search" and s.get("id") not in done:
        print(s["id"]); sys.exit(0)
print("")
PY
)
[ -n "$CALL_ID" ] || { echo "E2 REVISAR: sin function_call pendiente"; exit 1; }
echo "Worker pidio memory_search (call_id=$CALL_ID) — ejecutando contra el Engram REAL en el Mac..."

# Local execution: real Engram search, scoped to this project
QUERY=$(python3 - "$R1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for s in d.get("steps", []):
    if s.get("type") == "function_call" and s.get("name") == "memory_search":
        print(s.get("arguments", {}).get("query", "")); sys.exit(0)
print("")
PY
)
echo "Query del worker: $QUERY"
RESULT=$(cd /Users/albertmac11/Reguero/Proyectos/aistudio-bridge \
  && engram search "$QUERY" --limit 3 --project aistudio-bridge 2>/dev/null | head -40 || true)
# Escape for JSON embedding
RESULT_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' <<<"$RESULT")
echo "--- resultado del Engram local (lo que viaja al worker) ---"
echo "$RESULT"

BODY2=$(python3 - "$IID" "$EID" "$CALL_ID" "$RESULT_JSON" <<'PY'
import json, sys
iid, eid, call_id, result = sys.argv[1:5]
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "previous_interaction_id": iid,
    "environment": eid,
    "input": [{
        "type": "function_result",
        "name": "memory_search",
        "call_id": call_id,
        "result": json.loads(result),  # string result (API requirement)
    }],
}))
PY
)

R2="$LOG_DIR/e2_turn2.json"
echo "E2 turn 2: devolviendo el resultado real..."
CODE2=$(http_post 600 "$INTERACTIONS" "$BODY2" "$R2")
echo "HTTP $CODE2"
S2=$(json_field "$R2" "status")
OUT2=$(model_output "$R2")
echo "status: $S2 | uso: $(usage_summary "$R2")"
echo "--- respuesta final del worker ---"
echo "$OUT2"
echo "--------------------------------"
if [ "$CODE2" = "200" ] && [ "$S2" = "completed" ] && echo "$OUT2" | grep -q "SOL-VERDE-77"; then
  echo "E2 PASS: worker remoto respondió con contenido del Engram REAL del Mac."
  echo "El ciclo completo de la ruta C (worker → callback → Engram local) esta probado."
else
  echo "E2 REVISAR: $(head -c 1500 "$R2")"
  exit 1
fi
