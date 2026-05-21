# Chu-AI 本番環境

`testing` はCUIのテスト環境、`env` はBIG PAD向けの本番環境です。

## 構成

```text
env/
  setup.sh
  start_backend.sh
  start_frontend.sh
  .env.example
  .env
  backend/
    main.py
    requirements.txt
    knowledge/
  frontend/
    index.html
    style.css
    app.js
```

## 初回だけ行うこと

```bash
cd /Users/kosukeasakura/A_LIFE/TyubuAI
./env/setup.sh
```

その後、`env/.env` を開いて `API_KEY` を実際のGemini APIキーに置き換えます。

```text
API_KEY=AIzaから始まる実際のGemini APIキー
GEMINI_MODEL=gemini-2.5-flash
KNOWLEDGE_MODE_DEFAULT=all
```

`KNOWLEDGE_MODE_DEFAULT` は未指定時のナレッジ投入モードです。`all`（全知識をそのまま使う）または `search`（検索して一部を使う）を指定できます。

## 画面遷移とパスワード

1. パスワード画面（リロード時に毎回表示）
2. タイトル画面
3. チャット画面

パスワードは `commons` です。  
ホーム復帰時間は表示画面では変更せず、`env/.env` の `HOME_RETURN_SECONDS` で設定します（初期値: `30秒`）。  
チャット画面へ入ってから設定時間が経過すると、自動でタイトル画面へ戻ります。
現在のホーム復帰時間はバックエンド起動時ログに表示され、表示画面には表示しません。
ホームに戻る10秒前から、右下に `ホームに戻ります...(x)` の表示が出ます（`x` は残り秒数）。
チャット画面で操作（クリック、入力、スクロールなど）があるたびに、このタイマーはリセットされます。
チャット画面は「左60%: チャット」「右40%: 使用方法」で表示します。

## ナレッジモードの切り替え

フロントエンド画面にはナレッジモード選択を表示していません。  
会話画面からの送信は `all` 固定です。

1. APIで切り替える（リクエストごと）
- `/api/chat` のJSONに `knowledge_mode` を含めます。

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"予約は必要？","knowledge_mode":"all"}'
```

2. 既定値を切り替える（バックエンド全体）
- `env/.env` の `KNOWLEDGE_MODE_DEFAULT` を変更して、バックエンドを再起動します。

```text
KNOWLEDGE_MODE_DEFAULT=search
# または
KNOWLEDGE_MODE_DEFAULT=all
```

バックエンド起動時に、現在の既定モードと切り替え方法がログに表示されます。

## 毎回の起動

基本はターミナル1つで起動できます（バックエンドがフロントも配信します）。

```bash
cd /Users/kosukeasakura/A_LIFE/TyubuAI
./env/start_backend.sh
```

BIG PADのブラウザで開きます。

```text
http://127.0.0.1:8000
```

## 別PCやBIG PAD内のブラウザから開く場合

バックエンドは `0.0.0.0` で起動します。同じネットワーク内の別端末から見る場合は、起動しているPCのIPアドレスを使います。  
`https://` ではなく、必ず `http://` で開いてください（この構成はHTTP配信です）。

例:

```text
http://192.168.1.20:8000
```

フロントエンドは同一オリジンの `/api/chat` に接続します。

## 起動確認

バックエンド:

```bash
curl http://127.0.0.1:8000/api/health
```

UI:

```text
http://127.0.0.1:8000
```

## 旧構成（任意）

フロントだけを別ポートで配信したい場合は以下も使えます。

```bash
cd /Users/kosukeasakura/A_LIFE/TyubuAI
./env/start_frontend.sh
```

## よくあるエラー

`API_KEYに実際のGemini APIキーを設定してください。` と表示される場合:

`env/.env` の `API_KEY` が説明文のままです。Google AI Studioで発行した実際のキーに置き換えてください。

`python3.11 が見つかりません。` と表示される場合:

Python 3.11をインストールしてください。本番環境ではPython 3.11を前提にしています。

`Error: connect ENETUNREACH x.x.x.x:3000` と表示される場合:

BIG PADから起動PCへの経路がありません。起動PCで `./env/start_backend.sh` を再起動し、表示される `LAN URL` のアドレスへ `http://` で接続してください。  
`https://` や古いIPアドレス、`:3000` へのアクセスでは接続できないことがあります。
