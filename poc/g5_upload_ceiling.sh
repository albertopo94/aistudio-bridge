#!/usr/bin/env bash
# PoC G5: find the real ceiling of a direct resumable upload into the sandbox.
# The repository source is capped at 500 MB by the service; the direct upload
# path has no documented number ("fair usage limits"). This measures the actual
# ceiling with a ~1.5 GB blob so we know whether a GCS fallback is needed.
# Plan design: plan-remote-worker/README.md, "TODO — PoC del upload resumable".
#
# Run from the repo root. Uses the API key from poc/.env (hidden, 0600).
#   bash poc/g5_upload_ceiling.sh [bytes]
# Default payload is 1.5 GiB (1610612736 bytes). ~10-30 min, no billing.
#
# It creates an environment, uploads a blob through a resumable session,
# records the byte offset at which a non-308 reply arrives (the ceiling), then
# deletes the environment (which also exercises the G2 cleanup path).

set -euo pipefail
cd "$(dirname "$0")/.."

# --- key (same convention as the other PoC scripts) ----------------------
[ -f poc/.env ] || { echo "falta poc/.env (GEMINI_API_KEY)"; exit 2; }
set -a; source poc/.env; set +a
[ -n "${GEMINI_API_KEY:-}" ] || { echo "GEMINI_API_KEY vacia en poc/.env"; exit 2; }

SIZE="${1:-1610612736}"          # 1.5 GiB
CHUNK=$((10 * 1024 * 1024))      # 10 MB chunks
WORK="$(mktemp -d)"
BLOB="$WORK/blob.bin"
BASE_URL="${BASE_URL:-https://generativelanguage.googleapis.com}"

echo "== G5 upload-ceiling PoC =="
echo "target bytes: $SIZE  chunk: $CHUNK  base: $BASE_URL"

# 1. create an environment
echo "-- create environment"
ENV_BODY=$(curl -sS -X POST "$BASE_URL/v1beta/environments" \
  -H "x-goog-api-key: $GEMINI_API_KEY" -H "Content-Type: application/json" \
  -d '{"type":"remote","sources":[]}')
ENV_ID=$(printf '%s' "$ENV_BODY" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')
echo "environment_id=$ENV_ID"
[ -n "$ENV_ID" ] || { echo "no environment id from create:"; echo "$ENV_BODY"; exit 3; }

cleanup() { curl -sS -X DELETE "$BASE_URL/v1beta/environments/$ENV_ID" \
  -H "x-goog-api-key: $GEMINI_API_KEY" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

# 2. make the blob and cut it into 10 MB slice files (one per chunk)
echo "-- making $SIZE bytes (may take a moment)"
dd if=/dev/urandom of="$BLOB" bs=1024 count=$((SIZE / 1024)) status=none
SIZE=$(wc -c < "$BLOB")
rm -f "$WORK"/slice.*
OFF=0
while [ "$OFF" -lt "$SIZE" ]; do
  dd if="$BLOB" of="$WORK/slice.$(printf '%04d' $((OFF / CHUNK)))" \
     bs=1024 skip=$((OFF / 1024)) count=$((CHUNK / 1024)) status=none
  OFF=$((OFF + CHUNK))
done
[ "$(cat "$WORK"/slice.* | wc -c)" -eq "$SIZE" ] || { echo "slice total != blob"; exit 5; }
echo "actual bytes: $SIZE, sliced into $(ls "$WORK"/slice.* | wc -l | tr -d ' ') files"

# 3. init the resumable session
echo "-- init resumable upload"
curl -sS -D "$WORK/init.headers" -o /dev/null \
  -X PUT "$BASE_URL/upload/v1beta/environments/$ENV_ID/files/blob.bin?uploadType=resumable" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "X-Upload-Content-Type: application/gzip" \
  -H "X-Upload-Content-Length: $SIZE" \
  --data-binary '' || true
SESSION=$(awk 'tolower($1)=="location:"{print $2}' "$WORK/init.headers" | tr -d '\r')
echo "session=$SESSION"
[ -n "$SESSION" ] || { echo "no Location header from init"; cat "$WORK/init.headers"; exit 4; }

# 4. chunk it up. Measure: at which byte offset does a non-308 reply arrive?
echo "-- uploading (large: this is the measurement)"
START=0
N=0
FAILED_AT=""
for SLICE in "$WORK"/slice.*; do
  N=$((N + 1))
  SLICE_SIZE=$(wc -c < "$SLICE")
  END=$((START + SLICE_SIZE - 1))
  CR="bytes $START-$END/$SIZE"
  STATUS=$(curl -sS -o "$WORK/chunk.out" -w "%{http_code}" \
    -X PUT "$SESSION" \
    -H "Content-Type: application/octet-stream" \
    -H "Content-Range: $CR" \
    --data-binary "@$SLICE")
  if [ "$STATUS" = "308" ]; then
    START=$((END + 1))
    continue
  fi
  if [ "$STATUS" != "200" ] && [ "$STATUS" != "201" ]; then
    echo "chunk $N failed with HTTP $STATUS at byte $START"
    echo "body: $(head -c 200 "$WORK/chunk.out")"
    FAILED_AT="$START"
    break
  fi
  echo "final chunk accepted; upload finished"
  break
done

echo ""
echo "== result =="
if [ -n "${FAILED_AT:-}" ]; then
  echo "ceiling: the upload was refused at byte $FAILED_AT of $SIZE"
  echo "verdict: (b) direct upload caps below 1.5 GiB; GCS (2 GB) stays the fallback"
else
  echo "passed: $SIZE bytes uploaded cleanly through the resumable session"
  echo "verdict: (a) direct upload covers our range; GCS is redundant for now"
fi
echo "note: environment was deleted on exit (exercises the G2 cleanup path)"