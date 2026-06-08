# Chubu Commons AI

Chubu Commons AI は、中部大学のコモンズ、施設、学食、学部学科、大学案内情報を案内するチャットアプリです。エージェント名は「コモ」です。

FastAPI バックエンドと静的フロントエンドを Docker で起動し、同じ `8000` 番ポートで配信します。知識検索は常に `search + hybrid` です。クライアントが `all` を送ってもバックエンドで `search` に強制します。

## 現在の推奨構成

```text
Browser / BIG PAD
  -> http://<server>:8000
  -> FastAPI
      -> Gemini API
      -> Local PostgreSQL + pgvector   知識検索用
      -> Google Sheets                 chat_logs / feedback_logs 保存用
      -> src/backend/knowledge/*.md    回答元ナレッジ
```

ログとフィードバックは `LOG_STORAGE_MODE=google_sheets` で Google Sheets に保存します。学校 Wi-Fi では外部 PostgreSQL の `5432` が塞がれることがあるため、HTTPS 経由の Google Sheets API を標準にしています。

PostgreSQL 保存モードも残していますが、これはローカル検証や Google Sheets 障害時の保険です。Supabase 直結は推奨構成から外しています。

## 必要なもの

- Docker Desktop
- Gemini API キー
- Google Sheets API を有効化した Google Cloud Project
- Google Service Account JSON
- ログ保存先の Google スプレッドシート

Google Service Account のメールアドレスに、保存先スプレッドシートの `編集者` 権限を付けて共有してください。メールアドレスは `secrets/google-service-account.json` の `client_email` で確認できます。

## 新しいPCでの再現手順

1. リポジトリを取得する

```bash
git clone <repository-url>
cd CHU-AI
```

2. `.env` を作る

```bash
cp src/.env.example src/.env
```

Windows PowerShell:

```powershell
Copy-Item src\.env.example src\.env
```

3. Google Service Account JSON を置く

```text
secrets/google-service-account.json
```

`secrets/` は Git 管理外です。JSON キーは公開リポジトリに置かないでください。

4. `src/.env` を設定する

最低限、次を設定します。

```env
API_KEY=your-gemini-api-key
CHAT_LOG_ADMIN_API_KEY=十分長いランダム文字列

LOG_STORAGE_MODE=google_sheets
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/google-service-account.json
GOOGLE_SHEETS_SPREADSHEET_ID=your-google-spreadsheet-id
GOOGLE_CHAT_LOG_SHEET_NAME=chat_logs
GOOGLE_FEEDBACK_LOG_SHEET_NAME=feedback_logs

POSTGRES_DSN=postgresql://chu_ai:chu_ai@postgres:5432/chu_ai
```

通常そのままでよい値は `src/.env.example` に入っています。

5. 起動する

```bash
docker compose up -d --build
```

6. 初回だけ知識検索インデックスを作る

```bash
docker compose exec chu-ai python rebuild_hybrid_index.py
```

7. ブラウザで開く

```text
http://127.0.0.1:8000
```

ログイン用パスワード:

```text
commons
```

このパスワードは展示用の簡易ゲートです。フロントエンド内に含まれるため、本番認証や秘密情報の保護には使わないでください。

## 動作確認

起動確認:

```bash
curl -s http://127.0.0.1:8000/api/health
```

見る値:

```json
"chat_log_storage": "google_sheets"
"chat_log_db_configured": true
"search_backend_default": "hybrid"
"hybrid_db_configured": true
```

チャット送信確認:

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"中部大学の学食について教えて","knowledge_mode":"all"}'
```

レスポンスの `knowledge_mode` が `search` で、`chat_log_id` が返れば正常です。

フィードバック確認:

```bash
curl -s -X POST http://127.0.0.1:8000/api/feedback \
  -H "Content-Type: application/json" \
  -d '{"chat_log_id":"チャット応答で返ったchat_log_id","helpful":false,"feedback_type":"knowledge_missing","comment":"テスト"}'
```

管理APIで読み戻す:

```bash
curl -s http://127.0.0.1:8000/api/admin/chat-logs \
  -H "X-Admin-Key: 設定したCHAT_LOG_ADMIN_API_KEY"

curl -s http://127.0.0.1:8000/api/admin/feedback-logs \
  -H "X-Admin-Key: 設定したCHAT_LOG_ADMIN_API_KEY"
```

## 運用

コード更新だけなら:

```bash
git pull
docker compose up -d --build
```

ナレッジを追加・更新した時は、起動後に再インデックスします。

```bash
docker compose exec chu-ai python rebuild_hybrid_index.py
```

停止:

```bash
docker compose down
```

同じネットワーク内の別端末から開く場合:

```text
http://<起動PCのIPアドレス>:8000
```

`127.0.0.1` は起動PC自身からしか使えません。Windows では TCP `8000` をファイアウォールで許可してください。

## 保存先の切り替え

標準は Google Sheets です。

```env
LOG_STORAGE_MODE=google_sheets
```

ローカル PostgreSQL に保存したい場合:

```env
LOG_STORAGE_MODE=postgres
CHAT_LOG_POSTGRES_DSN=
```

`CHAT_LOG_POSTGRES_DSN` を空にすると、`POSTGRES_DSN` を使います。

Supabase 直結もコード上は可能ですが、学校 Wi-Fi で `5432` 接続が失敗したため標準運用では使いません。必要な場合だけ `CHAT_LOG_POSTGRES_DSN` に接続文字列を入れてください。

## API

主なAPI:

- `GET /api/health`
- `POST /api/chat`
- `POST /api/feedback`
- `GET /api/admin/chat-logs`
- `GET /api/admin/feedback-logs`

`/api/admin/*` は `X-Admin-Key` ヘッダが必要です。

`feedback_type` の候補:

- `helpful`
- `knowledge_missing`
- `wrong_answer`
- `hard_to_understand`
- `knowledge_request`
- `other`

## セキュリティ

公開してよいもの:

- ソースコード
- `src/.env.example`
- `Dockerfile`
- `docker-compose.yml`
- README

公開してはいけないもの:

- `src/.env`
- Gemini API キー
- Google Service Account JSON
- Google スプレッドシート ID
- `CHAT_LOG_ADMIN_API_KEY`
- ローカルDBやログ

`docker compose config` の出力には秘密情報が含まれる可能性があります。共有しないでください。

画面のログイン用パスワードはフロントエンドに含まれる簡易ゲートです。公開リポジトリに載ってもよい前提の値だけを扱い、重要な制御は管理APIキーや外部サービス側の権限で守ってください。

## ディレクトリ

```text
CHU-AI/
  Dockerfile
  docker-compose.yml
  README.md
  secrets/                         Git管理外
    google-service-account.json    Google Sheets保存用
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
```

詳細な実装説明は [src/README.md](src/README.md) を参照してください。
