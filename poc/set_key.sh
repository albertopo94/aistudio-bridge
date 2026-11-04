#!/usr/bin/env bash
# One-time helper: store the Gemini API key into poc/.env (0600).
# The key is read from the terminal and never printed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

umask 077
printf 'Pega la API key (no se muestra) y Enter: '
IFS= read -r -s k || { echo; echo "ERROR: no se leyo nada." >&2; exit 1; }
[ -n "$k" ] || { echo; echo "ERROR: la key esta vacia." >&2; exit 1; }

printf 'GEMINI_API_KEY=%s' "$k" > "$PWD/.env"
echo
echo "OK. Verificacion (sin mostrar la key):"
ls -l "$PWD/.env"
LEN=$(wc -c < "$PWD/.env" | tr -d ' ')
echo "Largo total del archivo: $LEN caracteres (la key suma el prefijo GEMINI_API_KEY=)"
# Verifica que el formato sea source-able sin imprimir el valor
set -a; source "$PWD/.env"; set +a
if [ -n "${GEMINI_API_KEY:-}" ]; then
  echo "Empieza con: ${GEMINI_API_KEY:0:3}... (formato OK, los scripts podran leerlo)"
else
  echo "ERROR: el archivo no parsea como GEMINI_API_KEY=..." >&2
  exit 1
fi
