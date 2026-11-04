#!/usr/bin/env bash
# Step 2: first Antigravity Agent interaction (the big unknown: does the
# preview agent accept a free-tier key?).
# Task: trivial — create a file and list the directory. No real data.
# Success: status "completed", and we capture interaction_id + environment_id
# into state.env for step 4.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

BODY=$(python3 - <<'PY'
import json
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "input": "Create a text file named hello.txt containing the word 'hola'. Then run `ls -la` and report what files you see.",
    "environment": "remote",
}))
PY
)

RESP="$LOG_DIR/step2_agent_first.json"
echo "Invocando al agente (puede tardar varios minutos)..."
CODE=$(http_post 600 "$INTERACTIONS" "$BODY" "$RESP")
echo "HTTP $CODE"

STATUS=$(json_field "$RESP" "status")
IID=$(json_field "$RESP" "id")
EID=$(json_field "$RESP" "environment_id")
OUT=$(model_output "$RESP")
echo "status:           $STATUS"
echo "interaction_id:   $IID"
echo "environment_id:   $EID"
echo "uso:              $(usage_summary "$RESP")"
echo "output_text:      ${OUT}

if [ "$CODE" = "200" ] && [ "$STATUS" = "completed" ]; then
  echo "PASS: el agente responded con una key de free tier (si lo es)."
  echo "interaction_id=$IID" > state.env
  echo "environment_id=$EID" >> state.env
  echo "Estado guardado en state.env para el paso 4."
elif [ "$CODE" = "403" ] || [ "$CODE" = "401" ] || echo "$STATUS" | grep -qi "quota\|billing\|not.*available"; then
  echo "FAIL probable: el agente preview puede requerir tier de pago."
  echo "Cuerpo de error:"; head -c 3000 "$RESP"; echo
  exit 1
else
  echo "REVISAR: estado inesperado. Cuerpo completo:"
  head -c 4000 "$RESP"; echo
  exit 1
fi
