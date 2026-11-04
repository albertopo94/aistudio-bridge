#!/usr/bin/env bash
# Step 5: test gemini-4-argon via agent_config on the Antigravity agent.
# Minimal task, tiny token budget as a safety net.
# Questions answered:
#   1. Does the free-tier key accept agent_config.model=gemini-4-argon?
#   2. If yes: real token cost of one trivial task, and does output come
#      via steps/model_output like the default model?
#   3. Does the sandbox come with an (empty?) default .agents/? — we ask
#      the agent to list it, which also preps the AGENTS.md injection question.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

BODY=$(python3 - <<'PY'
import json
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "agent_config": {
        "type": "antigravity",
        "model": "gemini-4-argon",
        "max_total_tokens": 100000,
    },
    "input": "Run these two commands and report their output exactly: (1) `find /app -maxdepth 3 -type f 2>/dev/null | head -50` (2) `cat /app/.agents/AGENTS.md 2>/dev/null || echo 'NO AGENTS.MD in /app/.agents'`. Also state which model you believe you are.",
    "environment": "remote",
}))
PY
)

RESP="$LOG_DIR/step5_argon.json"
echo "Invocando con gemini-4-argon (budget 100k tokens)..."
CODE=$(http_post 600 "$INTERACTIONS" "$BODY" "$RESP")
echo "HTTP $CODE"

if [ "$CODE" != "200" ]; then
  echo "REVISAR — cuerpo de respuesta:"
  head -c 3000 "$RESP"; echo
  exit 1
fi

STATUS=$(json_field "$RESP" "status")
EID=$(json_field "$RESP" "environment_id")
echo "status: $STATUS"
echo "environment_id: $EID"
echo "uso: $(usage_summary "$RESP")"
echo "--- model_output ---"
model_output "$RESP"
if [ "$EID" != "(null)" ]; then
  echo "environment_id=$EID" >> state.env
fi
