#!/usr/bin/env bash
# E3 replay (steps 3-4): apply the already-downloaded workspace tar onto a
# fresh local clone. Robust against permission-broken sandbox artifacts
# (__pycache__ written as root inside the tar): extract everything, purge
# .git and __pycache__, then overlay onto the local clone on a new branch.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh

WORKDIR="$POC_DIR/e3-work"
TAR="$WORKDIR/workspace.tar"
[ -f "$TAR" ] || { echo "Falta $TAR — correr e3_roundtrip.sh primero"; exit 1; }

# Fresh clone (previous attempts left permission-broken files behind)
rm -rf "$WORKDIR/clone" "$WORKDIR/extract"
git clone -q https://github.com/albertopo94/worker-poc-toy.git "$WORKDIR/clone"
cd "$WORKDIR/clone"
git checkout -qb worker/poc-e3

# Extract everything, ignoring tar errors on broken sandbox files
EXTRACT="$WORKDIR/extract"
mkdir -p "$EXTRACT"
tar xf "$TAR" -C "$EXTRACT" 2>/dev/null || true

REPO_IN_TAR="$EXTRACT/worker-poc-toy"
[ -d "$REPO_IN_TAR" ] || { echo "No encontré el repo en el tar:"; find "$EXTRACT" -maxdepth 2; exit 1; }

# Purge git metadata and Python cache artifacts from the worker's copy
rm -rf "$REPO_IN_TAR/.git"
find "$REPO_IN_TAR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
chmod -R u+rwX "$REPO_IN_TAR" 2>/dev/null || true

# Overlay the worker's files onto the local clone (no .git, no pycache)
(cd "$REPO_IN_TAR" && tar cf - --exclude='.git' --exclude='__pycache__' .) | (cd "$WORKDIR/clone" && tar xf -)

git add -A
echo "--- git status (cambios del worker aplicados en local) ---"
git status --short
echo "--- git diff --cached ---"
git diff --cached
echo "--- test suite en el Mac con el código del worker ---"
python3 tests/test_main.py
echo
echo "E3 PASS: loop ida-vuelta completo. El código del worker vive en la branch local worker/poc-e3."
echo "Clon: $WORKDIR/clone | TAR: $TAR"
