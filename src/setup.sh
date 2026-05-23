#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "python3.11 が見つかりません。Python 3.11をインストールしてください。"
  exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  echo "$SCRIPT_DIR/.env を作成しました。API_KEYを実際のGemini APIキーに置き換えてください。"
fi

python3.11 -m venv "$VENV_DIR"
. "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$BACKEND_DIR/requirements.txt"

echo "セットアップ完了。"
