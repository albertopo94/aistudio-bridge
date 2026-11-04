#!/usr/bin/env bash
# E3: full round-trip loop.
#   1. worker mounts the toy repo from GitHub and makes a bounded change
#   2. PoC downloads the changed directory as a tar (Environments Files API)
#   3. PoC applies the files on a LOCAL clone of the repo, on a new branch
#   4. PoC shows the git diff and runs the project's own test suite on the Mac
# Success: git diff shows exactly the requested change; tests pass locally.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh
load_env
mkdir -p "$LOG_DIR"

WORKDIR="$POC_DIR/e3-work"
rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"

# ---- 1. Worker task ----
BODY=$(python3 - <<'PY'
import json
si = (
    "You are a delegated code worker. You execute exactly one bounded task in "
    "the mounted repository and stop. Do not refactor, rename or touch files "
    "beyond the task. When done, run the test suite and report the result."
)
print(json.dumps({
    "agent": "antigravity-preview-09-2026",
    "agent_config": {"type": "antigravity", "model": "gemini-3.8-flash", "max_total_tokens": 200000},
    "system_instruction": si,
    "input": (
        "In the mounted repo at /workspace/worker-poc-toy: "
        "add a function `multiply(a, b)` to main.py that returns the product "
        "of a and b, with an English docstring. Add two tests for multiply in "
        "tests/test_main.py following the existing style. Do NOT change "
        "anything else in the repo. Run `python3 tests/test_main.py` and "
        "report the output. When finished, stop."
    ),
    "environment": {
        "type": "remote",
        "sources": [{
            "type": "repository",
            "source": "https://github.com/albertopo94/worker-poc-toy",
            "target": "/workspace/worker-poc-toy",
        }],
    },
}))
PY
)

R="$LOG_DIR/e3_worker.json"
echo "E3 paso 1: worker modifica el repo montado..."
CODE=$(http_post 600 "$INTERACTIONS" "$BODY" "$R")
echo "HTTP $CODE"
[ "$CODE" = "200" ] || { head -c 2000 "$R"; exit 1; }
STATUS=$(json_field "$R" "status")
EID=$(json_field "$R" "environment_id")
echo "status: $STATUS | env: $EID | uso: $(usage_summary "$R")"
echo "--- reporte del worker ---"
model_output "$R"
[ "$STATUS" = "completed" ] || { echo "E3 FAIL: worker no completo"; exit 1; }

# ---- 2. Download the changed directory as tar ----
TAR="$WORKDIR/workspace.tar"
echo "E3 paso 2: listando archivos del entorno..."
LIST="$LOG_DIR/e3_filelist.json"
curl -sS --max-time 120 -o "$LIST" \
  "https://generativelanguage.googleapis.com/v1beta/environments/$EID/files?recursive=true" \
  -H "x-goog-api-key: $GEMINI_API_KEY"
python3 - "$LIST" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for f in d.get("files", [])[:60]:
    print(f"  {f.get('type'):8s} {f.get('path')}")
PY

echo "Descargando /workspace como tar..."
DL=$(curl -sS --max-time 300 -w '%{http_code}' -o "$TAR" \
  "https://generativelanguage.googleapis.com/v1beta/environments/$EID/files/workspace?alt=media&recursive=true" \
  -H "x-goog-api-key: $GEMINI_API_KEY")
echo "HTTP $DL"
if [ "$DL" != "200" ]; then
  echo "E3 REVISAR: fallo la descarga. Probar rutas alternativas; ver $LIST"
  head -c 500 "$TAR"; exit 1
fi
echo "Tamaño del tar: $(wc -c < "$TAR" | tr -d ' ') bytes"

# ---- 3. Apply on a local clone, new branch ----
echo "E3 paso 3: clonando repo base y aplicando los cambios como diff..."
git clone -q https://github.com/albertopo94/worker-poc-toy.git "$WORKDIR/clone"
cd "$WORKDIR/clone"
git checkout -qb worker/poc-e3

EXTRACT="$WORKDIR/extract"
mkdir -p "$EXTRACT"
tar xf "$TAR" -C "$EXTRACT"
# Find the worker's copy of the repo inside the extracted tree
REPO_IN_TAR=$(find "$EXTRACT" -maxdepth 3 -type d -name "worker-poc-toy" | head -1)
[ -n "$REPO_IN_TAR" ] || { echo "E3 REVISAR: no encontré el repo dentro del tar:"; find "$EXTRACT" -maxdepth 2; exit 1; }
echo "Repo del worker encontrado en: $REPO_IN_TAR"

# Overlay worker files onto the local clone, EXCLUDING the worker's .git
# (the tar includes it; overlaying it would clobber the local clone's git state)
(cd "$REPO_IN_TAR" && tar cf - --exclude='.git' .) | (cd "$WORKDIR/clone" && tar xf -)
git add -A
echo "--- git status (cambios del worker) ---"
git status --short
echo "--- git diff --cached ---"
git diff --cached

# ---- 4. Run the project's own test suite ON THE MAC ----
echo "E3 paso 4: corriendo la test suite en el Mac con los cambios del worker..."
if python3 tests/test_main.py; then
  echo "TESTS PASS en local con el código del worker."
else
  echo "E3 FAIL: los tests fallan en local."
  exit 1
fi

echo
echo "E3 PASS: loop ida-vuelta completo."
echo "Clon local con la branch worker/poc-e3: $WORKDIR/clone"
echo "TAR descargado: $TAR"
