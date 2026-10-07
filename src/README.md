# Chubu Commons AI src

`src` はBIG PADや学内端末で動かすChubu Commons AI本体です。案内キャラクター名は「コモ」です。バックエンドはFastAPI、フロントエンドは静的HTML/CSS/JavaScriptです。

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
    style.css           共通画面とチャットUIのデザイン
    komo-stage.css      コモ表示領域とアバター演出
    app.js              画面制御、API通信、履歴生成
    avatar-puppet.js    コモの2Dパペット制御
    omikuji.js          おみくじの静的文章と抽選処理
    omikuji-tally.js    運勢別集計のブラウザ保存・復元
    omikuji-celebration.js 超レア当選時だけの画面演出と時間管理
    assets/avatar/      透過アバター画像
    vendor/pixi.min.js  ローカル配信用PixiJS
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
| `ROUTER_ENABLED` | 任意 | RAG前の事前分類を使うか | `true` |
| `ROUTER_MODEL` | 任意 | 事前分類に使うGeminiモデル | `gemini-3.1-flash-lite` |
| `ROUTER_CONFIDENCE_THRESHOLD` | 任意 | RAG検索をスキップする信頼度下限 | `0.85` |
| `KNOWLEDGE_MODE_DEFAULT` | 任意 | ナレッジ投入方法。実行時は常に `search` に強制 | `search` |
| `SEARCH_BACKEND_DEFAULT` | 任意 | 検索方式。実行時は常に `hybrid` に強制 | `hybrid` |
| `HOME_RETURN_SECONDS` | 任意 | 無操作時に会話を消去するまでの秒数（設定名は互換性のため維持） | `30` |
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
2. チャット画面

現在のパスワード:

```text
commons
```

これは展示用の簡易ゲートです。フロントエンド内に含まれるため、本番認証や秘密情報の保護には使わないでください。

チャット画面:

- 左上にアプリ名 `Chat-Como` と「中部大学・コモンズ案内」を表示
- 左側にチャット欄
- 右側にコモのアバター
- よくある質問を3列×2行の6ボタンで表示し、定型質問を送信可能
- 回答後は上段3件をおすすめ質問へ差し替え、下段には重複しない定番の質問を残す
- 回答中は思考中表示を出す
- 「会話を消去」で履歴・入力・おすすめ質問を初期化できる
- 一定時間操作がない場合も会話を初期化し、チャット画面は表示し続ける

おみくじ（PC画面向け）:

- コモの下にコンパクトな「コモのおみくじ」と木製の六角筒を表示。見出しと「引く」ボタンを横並びにして縦幅を抑え、コモの表示領域を広く取る。筒またはボタンを押すと約2秒の演出後に結果を表示する
- 六角筒が軽く揺れ、上の穴からおみくじの札が出る。運勢は札とその横の文章に表示する
- 最初に `frontend/omikuji.js` の `SUPER_RARE_PROBABILITY` で唯一の超レア「スーパー超大吉」を判定する。確率はこの定数1か所で変更できる（`0.001` なら0.1%、`0.5` なら50%）
- 超レア以外では従来どおり大吉・吉・中吉・小吉・末吉の5種類を等確率で抽選する（各運勢の全抽選に対する確率は `(1 - SUPER_RARE_PROBABILITY) / 5`）
- 通常は各運勢30件、計150件の短い文章を定数として持つ。超レアは固定の1文だけを別定数に持つ（合計151件）
- 文章は運勢ごとのシャッフル済み候補から選ぶ。同じ運勢の30件を一巡するまで文章は重複せず、一巡の境目でも直前の文章は避ける
- 抽選にAI・API・DBは使用しない。文章候補の残り位置はメモリのみで管理し、保存するのは運勢別の集計だけ
- 回数制限はなく、演出が終われば何度でも引ける。チャット回答中も利用できる
- コモは筒に視線を向け、頭・胴体・腕・足の重心をゆっくり追従させる。結果が出たら前を向き、一度うなずいて落ち着く。大吉では控えめに喜ぶ
- 超レアでは通常の2秒の抽選後、「…あれ？ この光は…！」を3.2秒見せる→一度だけの金色の発光・光の輪→4.8秒で巨大な当選名と王冠→6.5秒でコモの専用リアクションと156個の星・紙吹雪→13秒で第一波の花火とコモのアンコール→21秒で第二波の花火と `SUPER JACKPOT` 表示。専用演出は約33秒続き、31秒から2秒かけて消える
- 集中線・光の輪・発光の中心は当選タイトルのレイアウト中心に合わせる。画面サイズ変更時にも再計算し、登場アニメーションに中心が振り回されないようにする
- 専用リアクションは驚き、予備動作のかがみ、全身のジャンプ、両手を上げる喜び、着地の圧縮、姿勢の復帰を連動させる
- アンコールは両手を上げた二度のジャンプと全身の重心移動。会話の思考・回答中と動きを減らす設定では追加動作を行わない
- 右上にこのブラウザでの当たり回数を表示する。`？？？`（スーパー超大吉）を大吉の上に置き、各運勢と合計を表示する。結果が表示された時点で1回加算し、表示前に取り消した抽選は数えない
- 集計変数を `localStorage` の `chat-como.omikuji-tally.v1` に保存・復元する。保存開始前の旧集計は引き継がず、6種類の回数だけを小さなJSONに保存する（結果文・履歴・個人情報は保存しない）
- 同じブラウザ・同じURL（プロトコル・ホスト・ポート）なら、会話消去・無操作リセット・リロード・サーバー停止／再起動後も保持する。別端末とは共有しない。ブラウザデータの消去やプライベートウィンドウの終了では失われる
- 別タブの保存は `storage` イベントで反映する。保存不可・壊れた保存データでもチャットや抽選は動作し、保存不可の場合は「集計（未保存）」を表示する
- 専用画面演出はマウス・キーボード操作を遮らない。結果表示時点で再抽選可能になり、引き直し・会話消去・無操作リセットでは残った演出とタイマーを取り消す
- 動きを減らす設定では強い光・粒子・ジャンプを省くが、各段階の表示時間は同じ約33秒確保する。音声・追加ライブラリ・AI・API・DBは使用しない
- おみくじ動作中は通常の感情ポーズを重ねず、抽選から結果・再抽選へ直前の姿勢を引き継いで滑らかにつなぐ
- チャットの思考・回答演出を優先し、おみくじの動作が会話の表情や口パクを上書きしないようにする
- 「会話を消去」と無操作リセットで結果・抽選演出も初期化する（集計は保持）。`prefers-reduced-motion` 有効時は通常の抽選動作を短縮し、超レアの各表示段階の時間は変えない
- 確率の境界、通常文章の重複防止、超レア専用動作、集計の保存・復元は、リポジトリ直下で `node --test tests/frontend/omikuji.test.cjs` により追加依存なしで検証できる

アバター:

- PixiJSでテレビ頭、アンテナ、胴体、両腕、両脚を独立制御する
- 自動まばたき、呼吸、視線追従、重心移動、歩行、手振りを全身で表現する
- 静かな時間、小さな動き、中程度の動き、大きな行動、通常姿勢への復帰を順序立てて行う
- コモに触れると手振りを含むリアクションを返す
- 回答のタイプ表示中は口パクする
- `emotion` に応じて眉、目、口、頬、涙などを切り替える
- OSやブラウザの `prefers-reduced-motion` 設定に応じて動きを抑える
- 画像とPixiJSはローカル配信し、外部CDNへ依存しない

無操作時の会話リセット:

- 秒数は互換性のため `HOME_RETURN_SECONDS` で設定する
- 初期値は `30`
- チャット画面でクリック、入力、スクロールなどがあるとタイマーをリセットする
- 会話を消去する10秒前から右下にカウントダウンを表示する
- 無操作時も「会話を消去」を押した時も、チャット画面を維持したまま会話履歴を消す

会話履歴:

- ブラウザ上のメモリだけで保持する
- バックエンド側には保存しない
- API送信時に、過去会話を `text` に同梱してGeminiへ渡す
- 最大往復数は `CHAT_HISTORY_MAX_EXCHANGES` で設定する
- 何分以内の履歴を使うかは `CHAT_HISTORY_WINDOW_MINUTES` で設定する
- `CHAT_HISTORY_MAX_EXCHANGES=0` または `CHAT_HISTORY_WINDOW_MINUTES=0` の場合、履歴を使わない

おすすめ質問:

- GeminiがJSONで `recommended_questions` を3件返す
- フロントエンドは入力欄上に3列×2行の6ボタンを表示する。初期表示では定型質問群から重複なしで6件選ぶ
- 回答表示後、上段3ボタンをおすすめ質問へアニメーション付きで差し替える。下段3ボタンは初期質問を優先して残し、おすすめ質問と重複する場合だけ別の定型質問で補う
- ボタンを押すと、その文面をそのまま次の質問として送信する
- エラー、回答不可、またはおすすめ質問が3件揃わない場合は、既存ボタンの文言を変更しない
- 会話状態をリセットした場合は、初期ボタンへ戻す

回答分類:

- GeminiがJSONで `response_type` を返す
- GeminiがJSONで `emotion` も返す
- `emotion` は `neutral` / `happy` / `sad` / `angry` / `surprised` / `thinking` / `confused`
- 事前分類ルーターが `direct` と高信頼度で判定した `chat` / `usage` はRAG検索をスキップする
- 事前分類に失敗した場合、信頼度が低い場合、事実質問の可能性がある場合は必ずRAG検索する
- `chat` は挨拶、雑談、感謝、励ましなどの会話として扱う
- `knowledge` はナレッジに基づく施設、場所、制度、貸出物などの回答として扱う
- `unknown` は事実質問だが、現在のナレッジでは答えられない回答として扱う
- `clarify` は場所や条件が足りず、追加質問が必要な回答として扱う
- `usage` はアプリの使い方説明として扱う
- フィードバックUIは `knowledge` / `unknown` / `clarify` の回答に表示する

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
| `router_route` | 事前分類ルーターの振り分け。`direct` または `rag` |
| `router_response_type` | ルーターが推定した回答分類 |
| `router_confidence` | ルーターの信頼度。0.0〜1.0 |
| `router_reason` | ルーターがその振り分けにした理由 |
| `router_skipped_rag` | RAG検索をスキップしたか |
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
| `response_type` | string | `chat` / `knowledge` / `unknown` / `clarify` / `usage` の回答分類 |
| `recommended_questions` | string[] | 次におすすめする質問。回答可なら入力欄上の6ボタンのうち上段3件へ反映する |
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
- 無操作時の会話リセット秒数
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
