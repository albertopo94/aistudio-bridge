#!/usr/bin/env bash
# Step 4: sandbox persistence across conversations.
# Self-contained: creates a unique marker file in a fresh environment, then
# in a NEW interaction (no previous_interaction_id) reusing only the
# environment_id, asks the agent to read it back.
# Success: turn B reports the exact marker value.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

MARKER="poc-marker-$(date +%s)-$RANDOM"

BODY_A=$(python3 - "$MARKER" <<'PY'
import json, sys
marker = sys.argv[1]
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "input": f"Create a file named marker.txt containing exactly this text and nothing else: {marker}",
    "environment": "remote",
}))
PY
)

RA="$LOG_DIR/step4a_write.json"
echo "Turn A: escribiendo marker ($MARKER) en un sandbox nuevo..."
CODE_A=$(http_post 600 "$INTERACTIONS" "$BODY_A" "$RA")
S_A=$(json_field "$RA" "status")
EID=$(json_field "$RA" "environment_id")
echo "HTTP $CODE_A | status: $S_A | environment_id: $EID"
[ "$CODE_A" = "200" ] || { head -c 3000 "$RA"; echo; exit 1; }

# ---- New conversation, same environment, no conversation continuity ----
BODY_B=$(python3 - "$EID" <<'PY'
import json, sys
eid = sys.argv[1]
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "environment": eid,
    "input": "Read the file marker.txt and report its exact content. This is a fresh conversation; I have no context from before.",
}))
PY
)

RB="$LOG_DIR/step4b_read.json"
echo "Turn B: nueva conversacion (sin previous_interaction_id), mismo environment_id..."
CODE_B=$(http_post 600 "$INTERACTIONS" "$BODY_B" "$RB")
S_B=$(json_field "$RB" "status")
OUT_B=$(model_output "$RB")
echo "HTTP $CODE_B | status: $S_B | uso: $(usage_summary "$RB")"
echo "output_text: $OUT_B"

if [ "$CODE_B" = "200" ] && [ "$S_B" = "completed" ] && echo "$OUT_B" | grep -q "$MARKER"; then
  echo "PASS: los archivos persisten entre conversaciones dentro del mismo environment_id."
else
  echo "REVISAR: ver logs/step4b_read.json"
  exit 1
fi
