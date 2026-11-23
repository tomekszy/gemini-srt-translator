#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ROOT_ENV="$SCRIPT_DIR/.env"
VENV_ENV="$VENV_DIR/.env"

# Jeśli w katalogu roboczym też jest .env, a nie w SCRIPT_DIR, użyj go jako źródła
if [[ ! -f "$ROOT_ENV" && -f "$WORK_DIR/.env" ]]; then
  ROOT_ENV="$WORK_DIR/.env"
fi

# 1. Utwórz .venv, jeśli nie istnieje
if [[ ! -d "$VENV_DIR" ]]; then
  echo "Tworzę środowisko wirtualne w $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
fi

# 2. Aktywuj .venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 3. Zainstaluj google-genai, jeśli jeszcze nie jest dostępne
if ! python -c "import google.genai" 2>/dev/null; then
  echo "Instaluję google-genai w .venv..."
  pip install --upgrade pip >/dev/null
  pip install google-genai
fi

# 4. Wyciągnij GEMINI_API_KEY z głównego .env i skopiuj do .venv/.env
if [[ -f "$ROOT_ENV" ]]; then
  KEY_LINE="$(grep -E '^[[:space:]]*(export[[:space:]]+)?GEMINI_API_KEY[[:space:]]*=' "$ROOT_ENV" | head -n 1 || true)"
  if [[ -n "$KEY_LINE" ]]; then
    CLEAN_KEY_LINE="$(echo "$KEY_LINE" | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//')"
    # Zapisz/zaktualizuj GEMINI_API_KEY w .venv/.env
    mkdir -p "$VENV_DIR"
    if [[ -f "$VENV_ENV" ]] && grep -qE '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=' "$VENV_ENV"; then
      grep -vE '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=' "$VENV_ENV" > "$VENV_ENV.tmp" || true
      mv "$VENV_ENV.tmp" "$VENV_ENV"
    fi
    echo "$CLEAN_KEY_LINE" >> "$VENV_ENV"
  fi
fi

# 5. Załaduj zmienne z .venv/.env (a w razie braku, z głównego .env)
set -a
[[ -f "$VENV_ENV" ]] && source "$VENV_ENV"
[[ -z "${GEMINI_API_KEY:-}" && -f "$ROOT_ENV" ]] && source "$ROOT_ENV"
set +a

# 6. Odpal skrypt tłumaczący z przekazanymi argumentami
exec python "$SCRIPT_DIR/translate_srt_gemini.py" "$@"
