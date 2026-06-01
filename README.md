# Chu-AI

中部大学向けの案内チャットボットです。FastAPIバックエンドがAPIとフロントエンドを同じ `8000` 番ポートで配信します。検索は `legacy`（既存）と `hybrid`（PostgreSQL + ベクトル検索）を切り替えできます。

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
KNOWLEDGE_MODE_DEFAULT=search
SEARCH_BACKEND_DEFAULT=hybrid
HOME_RETURN_SECONDS=30
CHAT_HISTORY_MAX_EXCHANGES=5
CHAT_HISTORY_WINDOW_MINUTES=2
CHAT_LOG_ENABLED=true
CHAT_LOG_DB_PATH=data/chu_ai.sqlite3
POSTGRES_DSN=postgresql://chu_ai:chu_ai@postgres:5432/chu_ai
HYBRID_EMBEDDING_MODEL=gemini-embedding-001
HYBRID_EMBEDDING_DIM=768
```

## 起動

```bash
docker compose up --build
```

起動後、同じPCのブラウザで開きます。

```text
http://127.0.0.1:8000
```

`SEARCH_BACKEND_DEFAULT=hybrid` の場合は、初回だけインデックス作成を実行します。

```bash
docker compose exec chu-ai python rebuild_hybrid_index.py
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
| `SEARCH_BACKEND_DEFAULT` | `search` 時の検索方式 | `legacy` または `hybrid` |
| `HOME_RETURN_SECONDS` | チャット画面からホームへ戻る秒数 | `30` |
| `CHAT_HISTORY_MAX_EXCHANGES` | Geminiへ渡す過去会話の最大往復数 | `5` |
| `CHAT_HISTORY_WINDOW_MINUTES` | Geminiへ渡す過去会話の保持分数 | `2` |
| `CHAT_LOG_ENABLED` | 質問・回答ログをSQLiteへ保存するか | `true` |
| `CHAT_LOG_DB_PATH` | SQLiteログDBの保存先 | `data/chu_ai.sqlite3` |
| `POSTGRES_DSN` | hybrid検索用PostgreSQL接続文字列 | `postgresql://chu_ai:chu_ai@postgres:5432/chu_ai` |
| `HYBRID_EMBEDDING_MODEL` | 埋め込みモデル | `gemini-embedding-001` |
| `HYBRID_EMBEDDING_DIM` | 埋め込みベクトル次元 | `768` |

フロントエンドからの通常送信は `knowledge_mode: "search"` を指定します。APIを直接叩く場合のみ、リクエストごとに `search` / `all` を切り替えられます。

会話履歴はブラウザ上で保持し、API送信時に質問へ同梱します。`CHAT_HISTORY_MAX_EXCHANGES=0` または `CHAT_HISTORY_WINDOW_MINUTES=0` にすると、過去履歴をGeminiへ渡しません。

回答ログは `data/chu_ai.sqlite3` に保存します。DBファイルとテーブルはバックエンド起動時または初回保存時に自動作成します。保存内容は、質問時刻、質問文、回答、回答可否、参照した知識ファイル、使用した過去会話、おすすめ質問です。

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
- `data/`
- キャッシュ、ログ、ローカルDB

`.gitignore` と `.dockerignore` で秘密情報やローカル生成物は除外しています。

`docker compose config` は `src/.env` の値を展開して表示します。APIキーが含まれる可能性があるため、その出力は共有しないでください。

## ディレクトリ構成

```text
CHU-AI/
  Dockerfile
  docker-compose.yml
  README.md
  data/
    chu_ai.sqlite3      # 実行時に作成。git管理外
  src/
    README.md
    .env.example
    setup.sh
    start_backend.sh
    start_frontend.sh
    backend/
      main.py
      requirements.txt
      chu_ai/
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
