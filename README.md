# Chubu Commons AI

Chubu Commons AI は、中部大学のコモンズ、施設、学食、学部学科、大学案内情報を対話形式で案内する Web アプリです。エージェント名は「コモ」です。

このリポジトリでは、FastAPI バックエンドと静的フロントエンドを同じ `8000` 番ポートで配信します。知識検索は常に `search + hybrid` で動作し、`all` モードには戻りません。

詳しい実装説明は [src/README.md](src/README.md) を参照してください。この README は、起動、設定、確認、運用に必要な内容だけをまとめています。

## 構成

```text
Browser
  -> http://<server>:8000
  -> FastAPI
      -> /api/chat
      -> /api/health
      -> /api/admin/chat-logs
      -> Gemini API
      -> PostgreSQL + pgvector   検索用
      -> PostgreSQL / Supabase   chat_logs 保存用
      -> src/backend/knowledge/*.md
```

ポイント:

- フロントエンドは同一オリジンの `/api/chat` を呼びます
- 検索は常に PostgreSQL + pgvector の hybrid 検索です
- 会話ログ `chat_logs` は PostgreSQL に保存します
- `chat_logs` の保存先は、ローカル PostgreSQL でも Supabase でも構いません

## 主な機能

- 中部大学向け FAQ / 案内チャット
- Markdown ナレッジを使った hybrid 検索
- Gemini による回答生成
- おすすめ質問 3 件の自動提案
- 認証付きログ閲覧 API

## 必要なもの

- Docker Desktop
- Gemini API キー
- 任意: Supabase の PostgreSQL 接続情報

## 最短起動

1. `src/.env` を作る

```bash
cp src/.env.example src/.env
```

Windows PowerShell:

```powershell
Copy-Item src\.env.example src\.env
```

2. `src/.env` に最低限これを入れる

```env
API_KEY=AIzaから始まる実際のGemini APIキー
POSTGRES_DSN=postgresql://chu_ai:chu_ai@postgres:5432/chu_ai
CHAT_LOG_ADMIN_API_KEY=十分長いランダム文字列
```

3. 起動する

```bash
docker compose up -d --build
```

4. 初回だけ検索インデックスを作る

```bash
docker compose exec chu-ai python rebuild_hybrid_index.py
```

5. 開く

```text
http://127.0.0.1:8000
```

ログイン用パスワード:

```text
commons
```

停止:

```bash
docker compose down
```

## `.env` のおすすめ設定

### 1. ローカル PostgreSQL に全部保存する

```env
API_KEY=AIzaから始まる実際のGemini APIキー
GEMINI_MODEL=gemini-2.5-flash

KNOWLEDGE_MODE_DEFAULT=search
SEARCH_BACKEND_DEFAULT=hybrid

HOME_RETURN_SECONDS=180
CHAT_HISTORY_MAX_EXCHANGES=5
CHAT_HISTORY_WINDOW_MINUTES=2

CHAT_LOG_ENABLED=true
CHAT_LOG_POSTGRES_DSN=
CHAT_LOG_ADMIN_API_KEY=十分長いランダム文字列
CHAT_LOG_LIST_DEFAULT_LIMIT=50
CHAT_LOG_LIST_MAX_LIMIT=200

POSTGRES_DSN=postgresql://chu_ai:chu_ai@postgres:5432/chu_ai
POSTGRES_CONNECT_TIMEOUT_SECONDS=5

HYBRID_AUTO_INIT_DB=true
HYBRID_EMBEDDING_MODEL=gemini-embedding-001
HYBRID_EMBEDDING_DIM=768
HYBRID_CHUNK_SIZE=500
HYBRID_CHUNK_OVERLAP=80
HYBRID_KEYWORD_TOP_K=20
HYBRID_VECTOR_TOP_K=20
HYBRID_FINAL_TOP_K=6
HYBRID_RRF_K=60
```

`CHAT_LOG_POSTGRES_DSN` を空にすると、`POSTGRES_DSN` をそのまま使います。

### 2. 検索はローカル PostgreSQL、`chat_logs` だけ Supabase に保存する

```env
API_KEY=AIzaから始まる実際のGemini APIキー
GEMINI_MODEL=gemini-2.5-flash

KNOWLEDGE_MODE_DEFAULT=search
SEARCH_BACKEND_DEFAULT=hybrid

HOME_RETURN_SECONDS=180
CHAT_HISTORY_MAX_EXCHANGES=5
CHAT_HISTORY_WINDOW_MINUTES=2

CHAT_LOG_ENABLED=true
CHAT_LOG_POSTGRES_DSN=postgresql://postgres.<project-ref>:<password>@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres
CHAT_LOG_ADMIN_API_KEY=十分長いランダム文字列
CHAT_LOG_LIST_DEFAULT_LIMIT=50
CHAT_LOG_LIST_MAX_LIMIT=200

POSTGRES_DSN=postgresql://chu_ai:chu_ai@postgres:5432/chu_ai
POSTGRES_CONNECT_TIMEOUT_SECONDS=5

HYBRID_AUTO_INIT_DB=true
HYBRID_EMBEDDING_MODEL=gemini-embedding-001
HYBRID_EMBEDDING_DIM=768
HYBRID_CHUNK_SIZE=500
HYBRID_CHUNK_OVERLAP=80
HYBRID_KEYWORD_TOP_K=20
HYBRID_VECTOR_TOP_K=20
HYBRID_FINAL_TOP_K=6
HYBRID_RRF_K=60
```

Supabase では `5432` の session-mode pooler を使ってください。`6543` の transaction-mode はこの構成では使いません。

## 動作確認

### 起動確認

```bash
curl http://127.0.0.1:8000/api/health
```

見るポイント:

- `status: ok`
- `knowledge_mode_default: search`
- `search_backend_default: hybrid`
- `chat_log_storage: postgres`
- `chat_log_db_configured: true`

### チャット送信確認

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"中部大学の学食について教えて","knowledge_mode":"all"}'
```

見るポイント:

- レスポンスの `knowledge_mode` が `search`
- バックエンドログに `Knowledge selected: mode=hybrid`

### ログ保存確認

```bash
curl http://127.0.0.1:8000/api/admin/chat-logs \
  -H "X-Admin-Key: 設定した管理キー"
```

見るポイント:

- `items` に質問と回答が入っている
- `used_files` に参照した知識ファイルが入っている

## API

### `GET /api/health`

バックエンド起動確認用です。

### `POST /api/chat`

フロントエンドからの質問送信用です。

リクエスト例:

```json
{
  "text": "中部大学の学食について教えて",
  "knowledge_mode": "all"
}
```

補足:

- `knowledge_mode` は何を送っても、実行時は常に `search`
- 検索方式も常に `hybrid`

### `GET /api/admin/chat-logs`

認証付きのチャットログ一覧 API です。

ヘッダ:

```text
X-Admin-Key: 設定した管理キー
```

主なクエリ:

| 項目 | 型 | 役割 |
| --- | --- | --- |
| `limit` | int | 取得件数 |
| `offset` | int | 開始位置 |
| `can_answer` | bool | 回答可否で絞り込み |
| `knowledge_mode` | string | 例: `search` |
| `q` | string | 質問文・回答文の部分一致検索 |

## 知識を追加・更新する時

1. `src/backend/knowledge/` に Markdown を追加または更新する
2. インデックスを再構築する

```bash
docker compose exec chu-ai python rebuild_hybrid_index.py
```

これをしない限り、新しい知識は検索対象に入りません。

## 運用メモ

- `Enter` で送信されます
- 会話履歴はブラウザ側で保持し、送信時に質問文へ同梱します
- `chat_logs` には質問文、回答文、回答可否、使用知識、会話履歴、おすすめ質問が保存されます
- `CHAT_LOG_ADMIN_API_KEY` が空だとログ閲覧 API は無効です

## 別端末から開く

同じネットワーク内の端末からは、起動PCの IP アドレスで開きます。

```text
http://<起動PCのIPアドレス>:8000
```

注意:

- `https://` ではなく `http://`
- `127.0.0.1` は別端末からは使えない
- Windows では TCP `8000` をファイアウォールで許可する

## セキュリティ

公開してよいもの:

- ソースコード
- `src/.env.example`
- `Dockerfile`
- `docker-compose.yml`
- README

公開してはいけないもの:

- `src/.env`
- API キー
- Supabase パスワード
- `CHAT_LOG_ADMIN_API_KEY`
- ローカル DB やログ

`docker compose config` の出力には秘密情報が含まれる可能性があります。共有しないでください。

## ディレクトリ

```text
CHU-AI/
  Dockerfile
  docker-compose.yml
  README.md
  src/
    .env.example
    README.md
    backend/
      main.py
      rebuild_hybrid_index.py
      requirements.txt
      chu_ai/
      knowledge/
    frontend/
      index.html
      style.css
      app.js
  testing/
```

## ローカル Python で起動する場合

Docker を使わないなら:

```bash
./src/setup.sh
./src/start_backend.sh
```

ただし通常は Docker 起動を使ってください。
