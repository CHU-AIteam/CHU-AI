# Chu-AI

中部大学向けの案内チャットボットです。FastAPIバックエンドがAPIとフロントエンドを同じ `8000` 番ポートで配信します。

このREADMEは、リポジトリ全体の最短起動手順をまとめています。`src` 配下の詳しい構成や運用は [src/README.md](src/README.md) を参照してください。

## 推奨起動方法

Dockerで起動する方法を推奨します。Windows / macOS / Linuxで手順をそろえやすく、Python環境をPCに直接作らなくて済みます。

必要なもの:

- Docker Desktop
- Gemini APIキー

## 初回セットアップ

リポジトリ直下で実行します。

```bash
cp src/.env.example src/.env
```

Windows PowerShellの場合:

```powershell
Copy-Item src\.env.example src\.env
```

作成した `src/.env` を開き、`API_KEY` を実際のGemini APIキーに置き換えます。

```text
API_KEY=AIzaから始まる実際のGemini APIキー
GEMINI_MODEL=gemini-2.5-flash
KNOWLEDGE_MODE_DEFAULT=all
HOME_RETURN_SECONDS=30
CHAT_HISTORY_MAX_EXCHANGES=5
CHAT_HISTORY_WINDOW_MINUTES=2
```

## 起動

```bash
docker compose up --build
```

起動後、同じPCのブラウザで開きます。

```text
http://127.0.0.1:8000
```

2回目以降、コードや依存関係を変更していない場合は次で起動できます。

```bash
docker compose up
```

停止する場合:

```bash
docker compose down
```

## 別端末から開く

同じネットワーク内のスマホ、タブレット、BIG PADなどから開く場合は、Dockerを起動しているPCのIPアドレスを使います。

```text
http://<起動PCのIPアドレス>:8000
```

注意点:

- `https://` ではなく `http://` で開く
- `127.0.0.1` は起動PC自身を指すため、別端末では使わない
- Windowsで開けない場合は、Windows Defender FirewallでTCP `8000` の受信を許可する

## 主な設定

設定は `src/.env` で管理します。

| 項目 | 役割 | 例 |
| --- | --- | --- |
| `API_KEY` | Gemini APIキー | `AIza...` |
| `GEMINI_MODEL` | 使用するGeminiモデル | `gemini-2.5-flash` |
| `KNOWLEDGE_MODE_DEFAULT` | APIで指定がない場合のナレッジ投入方法 | `all` または `search` |
| `HOME_RETURN_SECONDS` | チャット画面からホームへ戻る秒数 | `30` |
| `CHAT_HISTORY_MAX_EXCHANGES` | Geminiへ渡す過去会話の最大往復数 | `5` |
| `CHAT_HISTORY_WINDOW_MINUTES` | Geminiへ渡す過去会話の保持分数 | `2` |

フロントエンドからの通常送信は `knowledge_mode: "all"` を指定します。APIを直接叩く場合のみ、リクエストごとに `search` / `all` を切り替えられます。

会話履歴はブラウザ上で保持し、API送信時に質問へ同梱します。`CHAT_HISTORY_MAX_EXCHANGES=0` または `CHAT_HISTORY_WINDOW_MINUTES=0` にすると、過去履歴をGeminiへ渡しません。

## 公開リポジトリでの注意

公開してよいもの:

- ソースコード
- `src/.env.example`
- Docker設定ファイル
- README

公開してはいけないもの:

- `src/.env`
- APIキー
- `src/backend/.venv/`
- キャッシュ、ログ、ローカルDB

`.gitignore` と `.dockerignore` で秘密情報やローカル生成物は除外しています。

`docker compose config` は `src/.env` の値を展開して表示します。APIキーが含まれる可能性があるため、その出力は共有しないでください。

## ディレクトリ構成

```text
CHU-AI/
  Dockerfile
  docker-compose.yml
  README.md
  src/
    README.md
    .env.example
    setup.sh
    start_backend.sh
    start_frontend.sh
    backend/
      main.py
      requirements.txt
      knowledge/
    frontend/
      index.html
      style.css
      app.js
  testing/
```

## Dockerなしで起動する場合

macOS / LinuxでPython 3.11が入っている場合は、ローカルスクリプトでも起動できます。

```bash
./src/setup.sh
./src/start_backend.sh
```

通常はDocker起動を使ってください。ローカル起動の詳細は [src/README.md](src/README.md) にまとめています。
