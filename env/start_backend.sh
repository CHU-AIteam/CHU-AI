#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
ENV_FILE="$SCRIPT_DIR/.env"

export PYTHONUTF8=1
export LANG=${LANG:-C.UTF-8}
export LC_ALL=${LC_ALL:-C.UTF-8}

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

if [ ! -d "$VENV_DIR" ]; then
  python3.11 -m venv "$VENV_DIR"
fi

. "$VENV_DIR/bin/activate"
python -m pip install -r "$BACKEND_DIR/requirements.txt"

cd "$BACKEND_DIR"
PORT="${BACKEND_PORT:-8000}"
RAW_KNOWLEDGE_MODE="${KNOWLEDGE_MODE_DEFAULT:-search}"
case "$RAW_KNOWLEDGE_MODE" in
  all|search)
    KNOWLEDGE_MODE_EFFECTIVE="$RAW_KNOWLEDGE_MODE"
    ;;
  *)
    KNOWLEDGE_MODE_EFFECTIVE="search"
    ;;
esac

RAW_HOME_RETURN_SECONDS="${HOME_RETURN_SECONDS:-30}"
case "$RAW_HOME_RETURN_SECONDS" in
  ''|*[!0-9]*)
    HOME_RETURN_SECONDS_EFFECTIVE="30"
    ;;
  *)
    if [ "$RAW_HOME_RETURN_SECONDS" -gt 0 ]; then
      HOME_RETURN_SECONDS_EFFECTIVE="$RAW_HOME_RETURN_SECONDS"
    else
      HOME_RETURN_SECONDS_EFFECTIVE="30"
    fi
    ;;
esac

LAN_IP=""
for IFACE in en0 en1; do
  CANDIDATE_IP=$(ifconfig "$IFACE" 2>/dev/null | awk '/inet / {print $2; exit}')
  if [ -n "$CANDIDATE_IP" ]; then
    LAN_IP="$CANDIDATE_IP"
    break
  fi
done

echo "Local URL: http://127.0.0.1:${PORT}"
if [ -n "$LAN_IP" ]; then
  echo "LAN URL:   http://${LAN_IP}:${PORT}"
fi
echo "Knowledge mode (default): ${KNOWLEDGE_MODE_EFFECTIVE}"
echo "  - 変更(既定): env/.env の KNOWLEDGE_MODE_DEFAULT=search|all を変更して再起動"
echo "  - 変更(都度): API bodyの knowledge_mode=search|all"
echo "Home return seconds: ${HOME_RETURN_SECONDS_EFFECTIVE}s"
echo "  - 変更(既定): env/.env の HOME_RETURN_SECONDS=30 などを変更して再起動"

exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
