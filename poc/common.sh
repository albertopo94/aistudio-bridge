#!/usr/bin/env bash
# Shared helpers for the Antigravity Agent PoC (Ruta A).
# - Never prints the API key.
# - All HTTP traffic goes only to Google's API endpoints.
# - Every response body is stored under poc/logs/ for evidence.

POC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API="https://generativelanguage.googleapis.com"
INTERACTIONS="$API/v1beta/interactions"
LOG_DIR="$POC_DIR/logs"

die() { echo "ERROR: $*" >&2; exit 2; }

load_env() {
  [ -f "$POC_DIR/.env" ] || die "falta $POC_DIR/.env — coloca tu key con el one-liner de poc/README.md"
  # shellcheck disable=SC1091
  set -a; source "$POC_DIR/.env"; set +a
  [ -n "${GEMINI_API_KEY:-}" ] || die "GEMINI_API_KEY vacia en poc/.env"
}

# http_post <timeout_s> <url> <json_body> <response_file>  -> prints HTTP status code
http_post() {
  local timeout=$1 url=$2 body=$3 out=$4
  local tmp="$out.tmp" code
  code=$(curl -sS --max-time "$timeout" -o "$tmp" -w '%{http_code}' \
    -X POST "$url" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$body") || { echo "ERROR: curl fallo (red o timeout) contra $url" >&2; exit 3; }
  mv "$tmp" "$out"
  echo "$code"
}

# json_field <file> <dot.path>  -> prints the value (null-safe)
json_field() {
  python3 - "$1" "$2" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("(no-json)")
    sys.exit(0)
v = d
for p in sys.argv[2].split("."):
    if isinstance(v, list) and p.lstrip("-").isdigit() and int(p) < len(v):
        v = v[int(p)]
    else:
        v = v.get(p) if isinstance(v, dict) else None
        if v is None:
            break
if v is None:
    print("(null)")
elif isinstance(v, (dict, list)):
    print(json.dumps(v)[:500])
else:
    print(v)
PYEOF
}

# model_output <file>  -> prints the text of the last model_output step
model_output() {
  python3 - "$1" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("(no-json)")
    sys.exit(0)
steps = d.get("steps", [])
outs = [s for s in steps if s.get("type") == "model_output"]
if not outs:
    print("(no model_output step)")
    sys.exit(0)
s = outs[-1]
parts = s.get("content", [])
for p in parts:
    if isinstance(p, dict) and p.get("text"):
        print(p["text"])
PYEOF
}

# usage_summary <file>  -> prints compact token usage
usage_summary() {
  python3 - "$1" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    u = d.get("usage", {})
    print(f"tokens: total={u.get('total_tokens')} input={u.get('total_input_tokens')} (cache={u.get('total_cached_tokens')}) output={u.get('total_output_tokens')}")
except Exception:
    pass
PYEOF
}
