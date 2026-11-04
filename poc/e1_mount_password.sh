#!/usr/bin/env bash
# E1: Mount the toy repo from GitHub as a repository source, then ask the
# worker for its calibration password.
# Answer:
#   - "BLUE-MOON-42"  -> harness reads AGENTS.md from a mounted repo (ideal)
#   - no/unknown      -> AGENTS.md only read from .agents/ or env root
#                        (gentle-shell will copy it there; implementation detail)
#   - invented value  -> contract via system_instruction must enforce it
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
        "model": "gemini-3.8-flash",
        "max_total_tokens": 150000,
    },
    "system_instruction": "You are a delegated worker. Before touching any project code, read and follow the project's AGENTS.md conventions. Answer factual questions precisely, quoting the source file when relevant.",
    "input": "The repo worker-poc-toy is mounted in your environment. Find it, then answer: what is your password? Quote the exact line where you found it.",
    "environment": {
        "type": "remote",
        "sources": [
            {
                "type": "repository",
                "source": "https://github.com/albertopo94/worker-poc-toy",
                "target": "/workspace/worker-poc-toy",
            }
        ],
    },
}))
PY
)

RESP="$LOG_DIR/e1_mount_password.json"
echo "E1: montando repo y preguntando el password (puede tardar)..."
CODE=$(http_post 600 "$INTERACTIONS" "$BODY" "$RESP")
echo "HTTP $CODE"
if [ "$CODE" != "200" ]; then
  echo "REVISAR: $(head -c 3000 "$RESP")"
  exit 1
fi
STATUS=$(json_field "$RESP" "status")
EID=$(json_field "$RESP" "environment_id")
echo "status: $STATUS | environment_id: $EID"
echo "uso: $(usage_summary "$RESP")"
echo "--- respuesta del worker ---"
model_output "$RESP"
echo "--------------------------------"
if model_output "$RESP" | grep -q "BLUE-MOON-42"; then
  echo "E1 RESULTADO: el worker LEYO el AGENTS.md del repo montado (caso ideal)."
  echo "environment_id=$EID" >> state.env
else
  echo "E1 RESULTADO: el worker NO respondio con el password del AGENTS.md."
  echo "=> gentle-shell debera inyectar el AGENTS.md del proyecto en .agents/ como inline source."
fi
