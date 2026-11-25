#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ROOT_ENV="$SCRIPT_DIR/.env"
VENV_ENV="$VENV_DIR/.env"

if [[ ! -f "$ROOT_ENV" && -f "$WORK_DIR/.env" ]]; then
  ROOT_ENV="$WORK_DIR/.env"
fi

if [[ ! -d "$VENV_DIR" || ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Tworzę środowisko wirtualne w $VENV_DIR..."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
elif [[ -f "$VENV_DIR/pyvenv.cfg" ]] && ! grep -qF "$VENV_DIR" "$VENV_DIR/pyvenv.cfg"; then
  echo "Wykryto przeniesienie katalogu – odbudowuję .venv w $VENV_DIR..."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if ! "$VENV_DIR/bin/python" -c "import google.genai" 2>/dev/null; then
  echo "Instaluję google-genai w .venv..."
  "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV_DIR/bin/python" -m pip install google-genai
fi

if [[ -f "$ROOT_ENV" ]]; then
  KEY_LINE="$(grep -E '^[[:space:]]*(export[[:space:]]+)?GEMINI_API_KEY[[:space:]]*=' "$ROOT_ENV" | head -n 1 || true)"
  if [[ -n "$KEY_LINE" ]]; then
    CLEAN_KEY_LINE="$(echo "$KEY_LINE" | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//')"
    mkdir -p "$VENV_DIR"
    if [[ -f "$VENV_ENV" ]] && grep -qE '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=' "$VENV_ENV"; then
      grep -vE '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=' "$VENV_ENV" > "$VENV_ENV.tmp" || true
      mv "$VENV_ENV.tmp" "$VENV_ENV"
    fi
    echo "$CLEAN_KEY_LINE" >> "$VENV_ENV"
  fi
fi

set -a
[[ -f "$VENV_ENV" ]] && source "$VENV_ENV"
[[ -z "${GEMINI_API_KEY:-}" && -f "$ROOT_ENV" ]] && source "$ROOT_ENV"
set +a

exec python "$SCRIPT_DIR/translate_srt_gemini.py" "$@"
