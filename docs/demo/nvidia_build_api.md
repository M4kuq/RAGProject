# Local NVIDIA Build API generation

This integration is for local prototype, research, and evaluation use only. It
does not add NVIDIA credentials, environment variables, Terraform resources,
ECS task settings, or frontend flags to the AWS deployment. The backend also
rejects NVIDIA generation when `APP_ENV` is not `local` or `test`.

NVIDIA receives the user question and the retrieved RAG context. Use only
public or demo documents when an NVIDIA model is selected.

## Configure the local key

Create or update the ignored `.env` file in the repository root. Never commit
the real key.

```env
NVIDIA_API_KEY=<key-created-on-build.nvidia.com>
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_TIMEOUT_SECONDS=60
VITE_ENABLE_NVIDIA_API=true
```

The local `docker-compose.yml` defaults the frontend flag to `true`, but keeping
it explicit in `.env` makes the local behavior clear. Restart the frontend and
backend containers after changing these values.

Do not paste or share `docker compose config` output after setting the key. The
command expands environment variables and can expose credentials.

## Chat model keys

When the local frontend flag is enabled, Chat shows this option:

| Label | `model_key` |
|---|---|
| NVIDIA Nemotron Super 49B (fast, recommended) | `nvidia:nvidia/llama-3.3-nemotron-super-49b-v1.5` |

The local default remains Local Qwen so that merely opening Chat never opts in
to external data transmission. When NVIDIA is enabled, a previously saved
Llama 3.3 70B selection is migrated to the recommended Nemotron Super model.
In the local smoke checks on 2026-07-22, Nemotron Super completed the short
request in 1.55 seconds; Llama 3.3 70B took about 69 seconds once and later hit
the 180-second timeout. DeepSeek V4 Flash was removed from the suggested models
after the opt-in live check returned a provider status error on 2026-07-23.

The `/api/v1/rag/ask` request and response schema is unchanged. Only the
existing `model_key` value is extended.

## Evaluation runs

The local evaluation form shows `nvidia` as a generation provider when
`VITE_ENABLE_NVIDIA_API=true`. Select the suggested model ID or type a different
NVIDIA API Catalog model ID:

- `generation_provider`: `nvidia`
- `generation_model`: catalog model ID without the `nvidia:` prefix

The existing rule still applies: a generation provider and model can be used
only when an answer-generation strategy is selected. NVIDIA is not added to
embedding, reranking, or graph extraction.

For the free prototype endpoint, reported token usage is retained and the
default estimated cost is `0.0 USD`. A future paid endpoint can use the existing
generation pricing override configuration.

## Live endpoint check

The PowerShell script reads only the NVIDIA values it needs from the repository
root `.env`, does not print the key, builds the backend test image once, and
calls the configured model once:

```powershell
.\scripts\test_nvidia_generation.ps1
```

To test one catalog model:

```powershell
.\scripts\test_nvidia_generation.ps1 -Models @("nvidia/llama-3.3-nemotron-super-49b-v1.5")
```

The live test is opt-in and is skipped by the normal test suite.

## Endpoint availability

Catalog availability and free endpoint status can change. If the live check
returns a 404 or model retirement error, confirm that Free Endpoint is still
available on the relevant Build page before changing the code:

- [Llama 3.3 Nemotron Super 49B v1.5](https://build.nvidia.com/nvidia/llama-3_3-nemotron-super-49b-v1_5)
- [NVIDIA NIM API quickstart](https://docs.api.nvidia.com/nim/docs/api-quickstart)
- [NVIDIA NIM product FAQ](https://docs.api.nvidia.com/nim/docs/product)

## Expected failure behavior

- missing key: NVIDIA selection returns `unsupported_model` without storing the
  user message;
- timeout, authentication, rate limiting, and upstream HTTP failures: only a
  coarse error category is logged or returned internally;
- provider response bodies, request headers, and API keys are never included in
  API errors or generation metadata;
- non-local environments: NVIDIA selection is rejected even if a key exists.

