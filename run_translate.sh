#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(pwd)"
ROOT_ENV="$SCRIPT_DIR/.env"

if [[ ! -f "$ROOT_ENV" && -f "$WORK_DIR/.env" ]]; then
  ROOT_ENV="$WORK_DIR/.env"
fi

if [[ -f "$ROOT_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_ENV"
  set +a
fi

if [[ ! -d "$SCRIPT_DIR/node_modules/@google/genai" || ! -d "$SCRIPT_DIR/node_modules/typescript" ]]; then
  echo "Instaluję zależności Node.js w $SCRIPT_DIR..."
  npm --prefix "$SCRIPT_DIR" install
fi

if [[ ! -f "$SCRIPT_DIR/dist/index.js" || "$SCRIPT_DIR/src/index.ts" -nt "$SCRIPT_DIR/dist/index.js" ]]; then
  echo "Kompiluję TypeScript do dist/..."
  npm --prefix "$SCRIPT_DIR" run build
fi

exec node "$SCRIPT_DIR/dist/index.js" "$@"