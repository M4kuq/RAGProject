# OpenAI API で回答生成を確認する手順

この手順は、UI の既定モデルである Local Qwen3.5 / LM Studio ではなく、回答生成だけを OpenAI API に切り替えて確認するためのものです。embedding と rerank は引き続き `fake` のままでよいです。

OpenAI 公式ドキュメントでは、Responses API が新規プロジェクト向けに推奨されています。このプロジェクトの `openai` provider も `POST /v1/responses` を使います。

参考:

- [Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [API keys](https://platform.openai.com/settings/organization/api-keys)
- [Using GPT-5.5](https://developers.openai.com/api/docs/guides/latest-model.md)

## 必要なもの

- OpenAI API key
- API key を使える OpenAI project / organization
- Docker Desktop または Docker Engine
- RAGProject の `.env`

API key は `.env` だけに設定します。README、docs、test fixture、issue、PR、チャット、ログには貼り付けません。
`docker compose config` は環境変数を展開して表示するため、実 API key 設定後の出力を共有しません。

## `.env` の設定

`.env.example` を `.env` にコピー済みであることを確認します。`.env` をエディタで開き、次を設定します。

```env
GENERATION_PROVIDER=openai
GENERATION_MODEL_NAME=gpt-5.5
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=30
```

`GENERATION_MODEL_NAME` は利用できるモデル名に合わせて変更できます。2026-05-22 時点の OpenAI 公式 latest model guide では `gpt-5.5` が案内されています。アカウントで利用できない場合は、OpenAI dashboard で利用可能なモデル名に変更します。

次はデモの再現性を優先するため、変更しないで構いません。

```env
EMBEDDING_PROVIDER=fake
RERANK_PROVIDER=fake
```

## Docker Compose でアプリを起動する

Windows PowerShell:

```powershell
docker compose build backend worker frontend
docker compose run --rm migrate
docker compose run --rm seed
docker compose up -d backend worker frontend
```

Ubuntu shell:

```bash
docker compose build backend worker frontend
docker compose run --rm migrate
docker compose run --rm seed
docker compose up -d backend worker frontend
```

`.env` を変更した後は backend / worker を再作成します。

```bash
docker compose up -d --build backend worker frontend
```

## UI で確認する

1. `http://localhost:5173` を開きます。
2. デモ用 admin または viewer でログインします。
3. seed 済み文書が ready であることを確認します。
4. Chat 画面で次のような質問を送信します。

```text
What is the core idea of Attention Is All You Need?
```

期待結果:

- 回答本文が `Fake answer ...` ではなく、通常の自然文になります。
- 回答内に `[1]` などの citation marker が含まれます。
- citation panel に参照元が表示されます。
- confidence が表示されます。
- API key は画面、レスポンス、ログに表示されません。

## pytest で実 API 呼び出しを確認する

このテストは明示的に有効化した場合だけ OpenAI API を呼びます。通常の CI では skip されます。

Windows PowerShell:

```powershell
cd backend
$env:RUN_OPENAI_GENERATION_TEST = "true"
$env:OPENAI_API_KEY = "<your-openai-api-key>"
$env:GENERATION_MODEL_NAME = "gpt-5.5"
uv run --extra dev pytest tests/test_openai_generation.py -k real_api -q
```

Ubuntu shell:

```bash
cd backend
export RUN_OPENAI_GENERATION_TEST=true
export OPENAI_API_KEY="<your-openai-api-key>"
export GENERATION_MODEL_NAME=gpt-5.5
uv run --extra dev pytest tests/test_openai_generation.py -k real_api -q
```

Docker の test image で実行する場合:

```bash
docker compose -f docker-compose.ci.yml build backend-test
docker compose -f docker-compose.ci.yml run --rm --no-deps \
  -e RUN_OPENAI_GENERATION_TEST=true \
  -e OPENAI_API_KEY \
  -e GENERATION_MODEL_NAME \
  backend-test pytest tests/test_openai_generation.py -k real_api -q
```

## Local Qwen3.5 に戻す

OpenAI API を使わない通常デモに戻す場合は、`.env` を次に戻して backend / worker を再作成します。

```env
GENERATION_PROVIDER=lmstudio
GENERATION_MODEL_NAME=lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M
LMSTUDIO_BASE_URL=http://host.docker.internal:1234/v1
LMSTUDIO_API_KEY=lm-studio
OPENAI_API_KEY=
```

```bash
docker compose up -d --build backend worker frontend
```

## よくある失敗

| Symptom | Cause | Fix |
|---|---|---|
| `/rag/ask` が `generation_failed` になる | `OPENAI_API_KEY` が未設定、無効、または対象 model を使えない | `.env` の key と model 名を確認して backend / worker を再作成します |
| 回答に citation marker がなく失敗する | モデルが `[1]` 形式の引用指示に従わなかった | もう一度質問します。続く場合は model 名を変更します |
| `Fake answer` のままになる | コンテナが古い設定のまま起動している | `.env` を確認し、`docker compose up -d --build backend worker frontend` を実行します |
| pytest が skip される | `RUN_OPENAI_GENERATION_TEST=true` が未設定 | 実 API を呼びたいときだけ環境変数を設定します |
