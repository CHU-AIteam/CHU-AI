# Chubu Commons AI src

`src` はBIG PADや学内端末で動かすChubu Commons AI本体です。エージェント名は「コモ」です。バックエンドはFastAPI、フロントエンドは静的HTML/CSS/JavaScriptです。

バックエンドがフロントエンドも配信するため、通常は `8000` 番ポートだけを開けば動きます。

## 役割

```text
src/
  .env.example          公開してよい設定テンプレート
  .env                  ローカル設定。APIキーを含むためコミットしない
  setup.sh              ローカルPython環境の初期セットアップ
  start_backend.sh      FastAPIバックエンドを起動
  start_frontend.sh     旧構成用。フロントだけを3000番で起動
  backend/
    main.py             FastAPIエントリーポイント
    rebuild_hybrid_index.py  hybrid検索インデックス再構築CLI
    requirements.txt    Python依存関係
    chu_ai/             バックエンド本体（API、サービス、設定）
    knowledge/          回答に使うMarkdownナレッジ
  frontend/
    index.html          画面構造
    style.css           画面デザイン
    app.js              画面制御、API通信、履歴生成
```

## 全体構成

通常構成:

```text
Browser
  -> http://<server>:8000/
  -> FastAPI backend
      -> /api/chat
      -> Gemini API
      -> backend/knowledge/*.md
```

ポイント:

- フロントエンドは同一オリジンの `/api/chat` にPOSTする
- `main.py`（`chu_ai/api.py`）が `frontend/` を静的ファイルとして配信する
- `start_frontend.sh` は旧構成やフロント単体確認用で、通常運用では使わない

## 環境変数

`src/.env` で設定します。初回は `src/.env.example` からコピーします。

```bash
cp src/.env.example src/.env
```

Windows PowerShell:

```powershell
Copy-Item src\.env.example src\.env
```

設定項目:

| 項目 | 必須 | 役割 | 既定値 |
| --- | --- | --- | --- |
| `API_KEY` | 必須 | Gemini APIキー | なし |
| `GEMINI_MODEL` | 任意 | Geminiモデル名 | `gemini-2.5-flash` |
| `KNOWLEDGE_MODE_DEFAULT` | 任意 | ナレッジ投入方法。実行時は常に `search` に強制 | `search` |
| `SEARCH_BACKEND_DEFAULT` | 任意 | 検索方式。実行時は常に `hybrid` に強制 | `hybrid` |
| `HOME_RETURN_SECONDS` | 任意 | チャット画面からホームへ戻る秒数 | `30` |
| `CHAT_HISTORY_MAX_EXCHANGES` | 任意 | Geminiへ渡す過去会話の最大往復数 | `5` |
| `CHAT_HISTORY_WINDOW_MINUTES` | 任意 | Geminiへ渡す過去会話の保持分数 | `2` |
| `CHAT_LOG_ENABLED` | 任意 | 質問・回答ログを保存するか | `true` |
| `LOG_STORAGE_MODE` | 任意 | `postgres` または `google_sheets` | `postgres` |
| `CHAT_LOG_POSTGRES_DSN` | 任意 | ログ保存先PostgreSQL DSN。空なら `POSTGRES_DSN` を流用 | 空 |
| `CHAT_LOG_ADMIN_API_KEY` | 任意 | 管理用ログAPIの固定キー。空なら閲覧APIを無効化 | 空 |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | 任意 | Google Sheets保存用Service Account JSONパス | 空 |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | 任意 | 保存先スプレッドシートID | 空 |
| `GOOGLE_CHAT_LOG_SHEET_NAME` | 任意 | chat_logs保存シート名 | `chat_logs` |
| `GOOGLE_FEEDBACK_LOG_SHEET_NAME` | 任意 | feedback_logs保存シート名 | `feedback_logs` |
| `CHAT_LOG_LIST_DEFAULT_LIMIT` | 任意 | ログ閲覧APIの既定取得件数 | `50` |
| `CHAT_LOG_LIST_MAX_LIMIT` | 任意 | ログ閲覧APIの最大取得件数 | `200` |
| `BACKEND_PORT` | 任意 | `start_backend.sh` の起動ポート | `8000` |
| `KNOWLEDGE_TOP_K` | 任意 | `search` 時に採用するナレッジファイル数 | `4` |
| `KNOWLEDGE_MAX_CHARS` | 任意 | 採用ナレッジ本文の最大文字数 | `26000` |
| `POSTGRES_DSN` | 任意 | hybrid検索用PostgreSQL DSN | 空 |
| `HYBRID_EMBEDDING_MODEL` | 任意 | 埋め込みモデル | `gemini-embedding-001` |
| `HYBRID_EMBEDDING_DIM` | 任意 | 埋め込み次元 | `768` |
| `HYBRID_CHUNK_SIZE` | 任意 | 1チャンクの文字数上限 | `500` |
| `HYBRID_CHUNK_OVERLAP` | 任意 | チャンク間の重複文字数 | `80` |
| `HYBRID_KEYWORD_TOP_K` | 任意 | キーワード検索の候補件数 | `20` |
| `HYBRID_VECTOR_TOP_K` | 任意 | ベクトル検索の候補件数 | `20` |
| `HYBRID_FINAL_TOP_K` | 任意 | RRF統合後の採用件数 | `6` |
| `HYBRID_RRF_K` | 任意 | RRF安定化係数 | `60` |
| `FRONTEND_PORT` | 任意 | `start_frontend.sh` の起動ポート | `3000` |

注意:

- `src/.env` はAPIキーを含むためコミットしない
- 公開リポジトリに置くのは `src/.env.example` だけ
- 設定変更後はバックエンドまたはDockerコンテナを再起動する

## Dockerで起動

リポジトリ直下で実行します。

```bash
docker compose up --build
```

開くURL:

```text
http://127.0.0.1:8000
```

停止:

```bash
docker compose down
```

Dockerでは `docker-compose.yml` が `src/.env` を読み込みます。`src/.env` がない、または `API_KEY` が未設定の場合はGemini呼び出しで失敗します。

`SEARCH_BACKEND_DEFAULT=hybrid` の場合は、初回にインデックス再構築を実行してください。

```bash
docker compose exec chu-ai python rebuild_hybrid_index.py
```

## ローカルスクリプトで起動

Python 3.11が必要です。

初回:

```bash
./src/setup.sh
```

起動:

```bash
./src/start_backend.sh
```

`start_backend.sh` は以下を行います。

- `src/.env` を読み込む
- `src/backend/.venv` がなければ作成する
- `requirements.txt` の依存関係をインストールする
- `uvicorn main:app --host 0.0.0.0 --port 8000` で起動する
- Local URLとLAN URLを表示する

## 画面仕様

画面遷移:

1. パスワード画面
2. タイトル画面
3. チャット画面

現在のパスワード:

```text
commons
```

これは展示用の簡易ゲートです。フロントエンド内に含まれるため、本番認証や秘密情報の保護には使わないでください。

チャット画面:

- 左側にチャット欄
- 右側に使用方法
- よくある質問ボタンから定型質問を送信可能
- 回答中は思考中表示を出す
- 一定時間操作がないとタイトル画面へ戻る

ホーム復帰:

- 秒数は `HOME_RETURN_SECONDS` で設定する
- 初期値は `30`
- チャット画面でクリック、入力、スクロールなどがあるとタイマーをリセットする
- ホームに戻る10秒前から右下にカウントダウンを表示する
- 無操作でホームへ戻った場合も、手動でホームへ戻った場合も会話履歴を消す

会話履歴:

- ブラウザ上のメモリだけで保持する
- バックエンド側には保存しない
- API送信時に、過去会話を `text` に同梱してGeminiへ渡す
- 最大往復数は `CHAT_HISTORY_MAX_EXCHANGES` で設定する
- 何分以内の履歴を使うかは `CHAT_HISTORY_WINDOW_MINUTES` で設定する
- `CHAT_HISTORY_MAX_EXCHANGES=0` または `CHAT_HISTORY_WINDOW_MINUTES=0` の場合、履歴を使わない

おすすめ質問:

- GeminiがJSONで `recommended_questions` を3件返す
- フロントエンドは回答表示後、入力欄上の既存3ボタンをおすすめ質問へアニメーション付きで差し替える
- ボタンを押すと、その文面をそのまま次の質問として送信する
- エラー、回答不可、またはおすすめ質問が3件揃わない場合は、既存ボタンの文言を変更しない
- ホームへ戻って会話状態をリセットした場合は、初期ボタンへ戻す

## 回答ログ保存

`CHAT_LOG_ENABLED=true` の場合、`/api/chat` の処理結果を保存します。保存先は `LOG_STORAGE_MODE` で切り替えます。

保存先:

- `LOG_STORAGE_MODE=postgres` ならPostgreSQLの `chat_logs` テーブルへ保存する
- `CHAT_LOG_POSTGRES_DSN` が空なら `POSTGRES_DSN` を流用する
- `LOG_STORAGE_MODE=google_sheets` ならGoogle Sheetsの `chat_logs` シートへ保存する
- 学校Wi-Fiで外部PostgreSQL接続が失敗する場合は `google_sheets` が候補

保存する主な項目:

| 項目 | 内容 |
| --- | --- |
| `chat_log_id` | Google Sheets保存時の文字列ID |
| `asked_at` | 聞かれた時間。日本時間のISO形式 |
| `question` | 今回の質問文 |
| `answer` | 画面に表示した回答 |
| `can_answer` | 回答可否 |
| `used_knowledge_files_json` | 参照した知識ファイル名のJSONB |
| `used_conversation_json` | Geminiへ渡した過去会話のJSONB |
| `recommended_questions_json` | 次におすすめする質問3件のJSONB |
| `knowledge_mode` | 実際に使ったナレッジモード。現在は `search` |
| `request_text` | フロントエンドから届いた履歴込みの全文 |
| `error_type` | APIエラーなどの種別 |
| `error_message` | APIエラーなどの詳細 |

## フィードバック保存

回答バブルの下に、`役に立った` / `足りなかった` のフィードバックUIを出します。送信された内容は、選択中の保存先に `feedback_logs` として保存します。

保存する主な項目:

| 項目 | 内容 |
| --- | --- |
| `chat_log_id` | どの回答に対する感想か |
| `helpful` | 役に立ったかどうか |
| `feedback_type` | `helpful`, `knowledge_missing`, `wrong_answer`, `hard_to_understand`, `knowledge_request`, `other` |
| `comment` | 自由記述 |
| `created_at` | 登録時刻 |

## API

### 起動確認

```bash
curl http://127.0.0.1:8000/api/health
```

レスポンス例:

```json
{
  "status": "ok",
  "model": "gemini-2.5-flash",
  "knowledge_mode_default": "search",
  "home_return_seconds": 30,
  "chat_history_max_exchanges": 5,
  "chat_history_window_minutes": 2,
  "chat_log_enabled": true,
  "chat_log_storage": "postgres",
  "chat_log_db_configured": true,
  "chat_log_admin_api_enabled": false,
  "search_backend_default": "hybrid",
  "hybrid_db_configured": true
}
```

### チャット

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"中部大学とは？","knowledge_mode":"search"}'
```

リクエスト:

| 項目 | 型 | 役割 |
| --- | --- | --- |
| `text` | string | ユーザーの質問。フロントエンドでは過去会話も含めて送る |
| `knowledge_mode` | string/null | リクエスト値。実行時はバックエンドで常に `search` に強制 |

レスポンス:

| 項目 | 型 | 役割 |
| --- | --- | --- |
| `answer` | string | Geminiが生成した回答 |
| `can_answer` | boolean | 回答できたかどうか |
| `recommended_questions` | string[] | 次におすすめする質問。回答可なら入力欄上の既存3ボタンへ反映する |
| `used_files` | string[] | 回答生成に使ったナレッジファイル |
| `knowledge_mode` | string | 実際に使ったナレッジモード |
| `chat_log_id` | string/null | 保存された回答ログID |

### フィードバック送信

```bash
curl -X POST http://127.0.0.1:8000/api/feedback \
  -H "Content-Type: application/json" \
  -d '{"chat_log_id":"チャット応答で返ったchat_log_id","helpful":false,"feedback_type":"knowledge_missing","comment":"知りたい情報が足りませんでした"}'
```

リクエスト:

| 項目 | 型 | 役割 |
| --- | --- | --- |
| `chat_log_id` | string | 対象の回答ログID |
| `helpful` | bool | 役に立ったかどうか |
| `feedback_type` | string | `helpful`, `knowledge_missing`, `wrong_answer`, `hard_to_understand`, `knowledge_request`, `other` |
| `comment` | string/null | 自由記述。500文字まで |

### 管理用ログ一覧

`CHAT_LOG_ADMIN_API_KEY` を設定した場合だけ使えます。

```bash
curl http://127.0.0.1:8000/api/admin/chat-logs \
  -H "X-Admin-Key: change-this-admin-key"
```

主なクエリ:

| 項目 | 型 | 役割 |
| --- | --- | --- |
| `limit` | int | 取得件数。既定値は `CHAT_LOG_LIST_DEFAULT_LIMIT` |
| `offset` | int | 取得開始位置 |
| `can_answer` | bool | 回答可否で絞り込み |
| `knowledge_mode` | string | 例: `search` |
| `q` | string | 質問文・回答文の部分一致検索 |

### 管理用フィードバック一覧

```bash
curl http://127.0.0.1:8000/api/admin/feedback-logs \
  -H "X-Admin-Key: change-this-admin-key"
```

主なクエリ:

| 項目 | 型 | 役割 |
| --- | --- | --- |
| `limit` | int | 取得件数 |
| `offset` | int | 取得開始位置 |
| `helpful` | bool | 役に立ったかどうかで絞り込み |
| `feedback_type` | string | 例: `knowledge_missing` |
| `q` | string | 質問文・回答文・コメントの部分一致検索 |

## ナレッジモード

現在は常に `search + hybrid` で動作します。クライアントが `knowledge_mode=all` を送っても、バックエンド側で `search` に上書きします。

hybrid検索では次を実行します。

- PostgreSQLのキーワード検索候補
- PostgreSQLのベクトル検索候補
- RRFで統合した上位チャンクを採用

現在のフロントエンドは通常送信時に `knowledge_mode: "search"` を指定します。APIリクエストで `knowledge_mode` を省略した場合や `all` を送った場合も、実際のレスポンスは `search` になります。

## ログ

起動時ログには以下を表示します。

- 現在のナレッジモード
- ホーム復帰秒数
- 会話履歴の最大往復数と保持分数
- 回答ログDBの有効/無効と保存先
- 管理用ログAPIの有効/無効
- Local URL
- LAN URL

チャットごとのログには以下を表示します。

- 採用したナレッジモード
- 使用したナレッジファイル
- 回答プレビュー
- Geminiへ投げる内容のうち、ナレッジ本文は `[知識]` として省略した確認用ログ

注意:

- `docker compose config` の出力は `src/.env` の値を含む
- APIキー入りのログや設定出力を共有しない

## 別端末から開く

同じネットワーク内の別端末から開く場合は、起動PCのLAN IPを使います。

```text
http://<起動PCのLAN IP>:8000
```

例:

```text
http://192.168.1.20:8000
```

確認すること:

- 起動PCと閲覧端末が同じネットワークにいる
- `https://` ではなく `http://` を使っている
- ポートは `8000`
- Windowsの場合、TCP `8000` の受信がファイアウォールで許可されている
- スマホのモバイル回線ではなく同じWi-Fiに接続している

## よくあるエラー

`API_KEYに実際のGemini APIキーを設定してください。`

`src/.env` の `API_KEY` がテンプレートのままです。Google AI Studioで発行した実際のキーに置き換えてください。

`429 RESOURCE_EXHAUSTED`

Gemini API側の利用上限、課金上限、または月額上限に達しています。コードではなくAPIプロジェクト側の制限です。

`python3.11 が見つかりません。`

ローカルスクリプト起動に必要なPython 3.11が入っていません。Docker起動に切り替えるか、Python 3.11をインストールしてください。

スマホや別PCから開けない

`127.0.0.1` ではなく起動PCのLAN IPを使ってください。WindowsではファイアウォールでTCP `8000` を許可してください。

`Error: connect ENETUNREACH x.x.x.x:3000`

古いフロント単体構成、古いIP、または `3000` 番に接続しています。通常構成では `http://<起動PCのIP>:8000` を使ってください。

## 変更時の確認

READMEや設定を変更した後は、最低限以下を確認します。

```bash
sh -n src/setup.sh
sh -n src/start_backend.sh
sh -n src/start_frontend.sh
python3.11 -m py_compile src/backend/main.py
docker compose config --quiet
```

Docker起動まで確認する場合:

```bash
docker compose up --build
```
