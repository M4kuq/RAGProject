# モデル選択 UI と API key 設定

Chat 画面は Microsoft Copilot に近い形で、上部のモデル選択から回答生成モデルを切り替えます。デフォルトは `Local Qwen3.5` です。ローカル実行は LM Studio を使います。

## 選択肢

| UI label | Provider | Model | 必要な設定 |
|---|---|---|---|
| Local Qwen3.5 | `lmstudio` | `lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M` | LM Studio server と Qwen3.5 |
| GPT 5.5 | `openai` | `gpt-5.5` | `OPENAI_API_KEY` |
| GPT 5.4 | `openai` | `gpt-5.4` | `OPENAI_API_KEY` |
| Claude | `anthropic` | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| Gemini | `gemini` | `gemini-2.5-flash` | `GEMINI_API_KEY` |

UI には fake model を表示しません。fake は CI と自動回帰テスト用の既定値として残します。

## Local Qwen3.5 を LM Studio で使う

`.env` に次を設定します。

```env
GENERATION_PROVIDER=lmstudio
GENERATION_MODEL_NAME=lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M
LMSTUDIO_BASE_URL=http://host.docker.internal:1234/v1
LMSTUDIO_API_KEY=lm-studio
```

LM Studio 側で次を行います。

1. LM Studio を起動します。
2. `https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF` から `lmstudio-community/Qwen3.5-9B-GGUF` をダウンロードします。
3. Qwen3.5 をロードします。
4. Server タブで Local Server を開始します。
5. base URL が `http://localhost:1234/v1` であることを確認します。
6. `GET http://localhost:1234/v1/models` で返る model id を確認します。LM Studio が別のIDを返す場合は、`.env` の `GENERATION_MODEL_NAME` と UI の model 値をそのIDに合わせます。

Docker 上の backend からホスト側 LM Studio に接続するため、compose では `host.docker.internal` を使います。Chat 画面では `Local Qwen3.5` が初期選択されます。

実サーバーだけを先に確認する場合は次を使います。

```powershell
.\scripts\test_lmstudio_generation.ps1
```

Ubuntu では次を使います。

```bash
bash scripts/test_lmstudio_generation.sh
```

## OpenAI GPT を使う

`.env` に次を設定します。

```env
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_BASE_URL=https://api.openai.com/v1
```

UI で `GPT 5.5` または `GPT 5.4` を選択します。
利用中の OpenAI アカウントでモデル名が有効ではない場合は、UI のモデル定義と `.env` の `GENERATION_MODEL_NAME` を利用可能なモデル名に合わせます。

## Claude を使う

`.env` に次を設定します。

```env
ANTHROPIC_API_KEY=<your-anthropic-api-key>
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_VERSION=2023-06-01
```

UI で `Claude` を選択します。モデル名は初期値として `claude-sonnet-4-20250514` を使います。利用できない場合は frontend の `MODEL_OPTIONS` 相当の値を変更します。

## Gemini を使う

`.env` に次を設定します。

```env
GEMINI_API_KEY=<your-gemini-api-key>
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

UI で `Gemini` を選択します。モデル名は初期値として `gemini-2.5-flash` を使います。

クラウドモデルを選択した場合は、質問文と取得済みコンテキストが各 provider の API に送られます。デモでは公開可能な文書だけを投入します。

## LM Studio と OpenAI互換 API

LM Studio は OpenAI互換 endpoint を提供します。このプロジェクトでは `GENERATION_PROVIDER=lmstudio` のとき、`/v1/chat/completions` にリクエストします。OpenAI のクラウド API でローカル LM Studio や Qwen3.5 を実行するわけではありません。

## API key の扱い

API key は `.env` だけに設定します。README、docs、test fixture、PR コメント、ログには貼り付けません。`docker compose config` は環境変数を展開して表示するため、API key 設定後の出力を共有しません。
