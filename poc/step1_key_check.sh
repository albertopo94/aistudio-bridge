#!/usr/bin/env bash
# Step 1: validate the API key with a single tiny call (free-tier model).
# Success: HTTP 200 with a non-empty candidate. Prints NO key material.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env

mkdir -p "$LOG_DIR"

# Minimal text generation: one word, max_tokens=10, cheapest free-tier model.
BODY=$(python3 - <<'PY'
import json
print(json.dumps({
    "model": "gemini-3.5-flash-lite",
    "contents": [{"parts": [{"text": "Say exactly: OK"}]}],
    "generationConfig": {"maxOutputTokens": 10, "temperature": 0},
}))
PY
)

RESP="$LOG_DIR/step1_key_check.json"
CODE=$(http_post 60 "$API/v1beta/models/gemini-3.5-flash-lite:generateContent" "$BODY" "$RESP")
echo "HTTP $CODE"
if [ "$CODE" = "200" ]; then
  TEXT=$(json_field "$RESP" "candidates.0.content.parts.0.text")
  echo "Modelo respondio: ${TEXT}"
  echo "PASS: la key funciona."
else
  echo "FAIL: la key no funciona. Cuerpo de error:"
  head -c 2000 "$RESP"; echo
  exit 1
fi
