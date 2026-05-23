# Chu-AI

中部大学向け案内チャットボットです。FastAPIバックエンドがフロントエンドも配信するため、Dockerでは1コンテナで起動します。

## Dockerで起動

初回だけ、公開用テンプレートからローカル設定ファイルを作ります。

```bash
cp src/.env.example src/.env
```

Windows PowerShellでは次でも同じです。

```powershell
Copy-Item src\.env.example src\.env
```

`src/.env` を開き、`API_KEY` を実際のGemini APIキーに置き換えます。

```text
API_KEY=AIzaから始まる実際のGemini APIキー
GEMINI_MODEL=gemini-2.5-flash
KNOWLEDGE_MODE_DEFAULT=all
HOME_RETURN_SECONDS=30
```

起動します。

```bash
docker compose up --build
```

2回目以降は通常これで起動できます。

```bash
docker compose up
```

ブラウザで開きます。

```text
http://127.0.0.1:8000
```

同じネットワーク内の別端末から開く場合は、Dockerを起動しているPCのIPアドレスを使います。

```text
http://<PCのIPアドレス>:8000
```

Windowsで別端末から開けない場合は、Windows Defender FirewallでTCP `8000` の受信を許可してください。

## 公開リポジトリ運用

`src/.env` はAPIキーを含むため、コミットしません。公開してよいのは `src/.env.example` だけです。

`.gitignore` と `.dockerignore` で `.env`、仮想環境、キャッシュ、ログ、DBファイルを除外しています。

`docker compose config` はローカルの `src/.env` を展開して表示するため、APIキー入りの出力を共有しないでください。

## Dockerなしで起動

macOS/Linuxでは従来通り以下でも起動できます。

```bash
./src/start_backend.sh
```

詳しい運用手順は [src/README.md](src/README.md) を参照してください。
