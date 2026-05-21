#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PORT="${FRONTEND_PORT:-3000}"
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
  echo "起動PCのLAN_IP=${LAN_IP}"
  echo "LAN URL:   http://${LAN_IP}:${PORT}"
fi

cd "$FRONTEND_DIR"
exec python3.11 -m http.server "$PORT" --bind 0.0.0.0
