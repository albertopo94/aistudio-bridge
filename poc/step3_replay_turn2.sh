#!/usr/bin/env bash
# Replay step 3 turn 2 using IDs from a previous turn 1 in $1 (json file).
# Usage: step3_replay_turn2.sh logs/step3_turn1.json
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

R1="$1"
S1=$(json_field "$R1" "status")
IID=$(json_field "$R1" "id")
EID=$(json_field "$R1" "environment_id")
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
[ -n "$CALL_ID" ] || { echo "No hay call pendiente en $R1"; exit 1; }

# Inspect what the agent actually requested
echo "Solicitud original del agente:"
python3 - "$R1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for s in d.get("steps", []):
    if s.get("type") == "function_call" and s.get("name") == "memory_search":
        print("  name:", s.get("name"))
        print("  args:", json.dumps(s.get("arguments")))
PY

FAKE_MEMORY='{"memories":[{"title":"Decision de arquitectura","content":"La memoria debe vivir local-first en el Mac. El sandbox remoto es solo un cache de 7 dias.","type":"decision","relevance":0.97}]}'

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
        "result": fake,
    }],
}))
PY
)

R2="$LOG_DIR/step3_replay_turn2.json"
echo "Reintentando turn 2 con result como JSON string..."
CODE2=$(http_post 600 "$INTERACTIONS" "$BODY2" "$R2")
echo "HTTP $CODE2"
S2=$(json_field "$R2" "status")
OUT2=$(model_output "$R2")
echo "status: $S2 | uso: $(usage_summary "$R2")"
echo "output_text:"
echo "$OUT2"

if [ "$CODE2" = "200" ] && [ "$S2" = "completed" ]; then
  echo "PASS: el ciclo completo de function calling stateful funciona."
else
  echo "REVISAR: $(head -c 1500 "$R2")"
  exit 1
fi
