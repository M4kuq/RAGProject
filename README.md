# RAGProject

## Phase3 GraphRAG Final Handoff

Phase3 GraphRAG documentation starts in
[`docs/phase3/README.md`](docs/phase3/README.md). PR-54 is the final
hardening and demo-docs handoff for the text GraphRAG path delivered through
PR-46 to PR-53. It does not add a new retrieval strategy or evaluation metric.

Start with:

- [`docs/phase3/graph_rag_final_readme.md`](docs/phase3/graph_rag_final_readme.md)
- [`docs/phase3/graph_rag_demo_scenario.md`](docs/phase3/graph_rag_demo_scenario.md)
- [`docs/phase3/graph_rag_acceptance_checklist.md`](docs/phase3/graph_rag_acceptance_checklist.md)
- [`docs/phase3/graph_rag_manual_test_cases.md`](docs/phase3/graph_rag_manual_test_cases.md)
- [`docs/phase3/phase3_roadmap.md`](docs/phase3/phase3_roadmap.md)
- [`docs/phase3/graph_rag_architecture.md`](docs/phase3/graph_rag_architecture.md)
- [`docs/phase3/graph_retrieval_strategy.md`](docs/phase3/graph_retrieval_strategy.md)
- [`docs/phase3/neo4j_optional_backend.md`](docs/phase3/neo4j_optional_backend.md)
- [`docs/phase3/retrieval_cache_foundation.md`](docs/phase3/retrieval_cache_foundation.md)
- [`docs/phase3/graph_evaluation_design.md`](docs/phase3/graph_evaluation_design.md)
- [`docs/phase3/security_redaction_policy.md`](docs/phase3/security_redaction_policy.md)
- [`docs/demo/corpus_neo4j_demo.md`](docs/demo/corpus_neo4j_demo.md)

Safe PR-54 smoke:

```powershell
scripts\smoke_phase3_graph_rag.ps1
```

```sh
sh scripts/smoke_phase3_graph_rag.sh
```

The smoke is non-destructive. It checks Compose config, GraphRAG docs, helper
scripts, and fixture presence without printing `.env` values or requiring
external providers, Neo4j, Redis, OCR, cloud resources, or model downloads.

Reproducible local demo corpus and Neo4j-backed GraphRAG comparison:

```powershell
scripts\neo4j_demo.ps1
```

```sh
sh scripts/neo4j_demo.sh
```

See [Demo Corpus and Neo4j Demo Stack](docs/demo/corpus_neo4j_demo.md) for
the manifest-driven corpus rebuild, projection, and provider comparison runbook.

## Phase2 Handoff

Phase2 final demo, acceptance, smoke, and Phase3 handoff material lives under
[`docs/phase2/README.md`](docs/phase2/README.md).

Start with:

- [`docs/phase2/phase2_demo_scenario.md`](docs/phase2/phase2_demo_scenario.md)
- [`docs/phase2/phase2_manual_test_cases.md`](docs/phase2/phase2_manual_test_cases.md)
- [`docs/phase2/phase2_acceptance_checklist.md`](docs/phase2/phase2_acceptance_checklist.md)
- [`docs/phase2/phase2_known_limitations.md`](docs/phase2/phase2_known_limitations.md)
- [`docs/phase2/phase3_handoff.md`](docs/phase2/phase3_handoff.md)

Safe local smoke:

```powershell
scripts\smoke_phase2.ps1
```

```sh
sh scripts/smoke_phase2.sh
```

The Phase2 smoke does not run destructive cleanup, does not print `.env`
values, and does not require external API keys, LangSmith, GPU, or mandatory
heavy model downloads.

## Local Kubernetes Baseline

PR-43 adds a local kind/minikube baseline without replacing Docker Compose. See
[`docs/phase2/kubernetes_local_baseline.md`](docs/phase2/kubernetes_local_baseline.md).

Validate manifests:

```powershell
python scripts\validate_k8s_manifests.py
kubectl kustomize k8s/local
```

```sh
python scripts/validate_k8s_manifests.py
kubectl kustomize k8s/local
```

Build and load local images before applying the manifests:

```powershell
scripts\k8s_load_images.ps1 -Runtime kind
kubectl apply -k k8s/local
scripts\k8s_smoke.ps1
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

```sh
K8S_RUNTIME=kind sh scripts/k8s_load_images.sh
kubectl apply -k k8s/local
sh scripts/k8s_smoke.sh
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

The committed Kubernetes Secret is a template with local placeholders only.
Do not commit real Kubernetes secrets, `.env` values, API keys, DB dumps,
Qdrant data, generated logs, or debug artifacts.

RAGProject 縺ｯ縲∵枚譖ｸ繧｢繝・・繝ｭ繝ｼ繝峨∵歓蜃ｺ縲…hunking縲‘mbedding縲＿drant index縲〉etrieval縲〉erank縲∝屓遲皮函謌舌…itation縲…onfidence縲‘valuation縲｀CP stdio / Streamable HTTP server 縺ｾ縺ｧ繧・Docker Compose 縺ｮ繝ｭ繝ｼ繧ｫ繝ｫ迺ｰ蠅・〒遒ｺ隱阪☆繧・Phase1 RAG 繝昴・繝医ヵ繧ｩ繝ｪ繧ｪ縺ｧ縺ゅｋ縲・
Phase1 縺ｮ逶ｮ逧・・縲√け繝ｩ繧ｦ繝牙・髢九〒縺ｯ縺ｪ縺上∫ｬｬ荳芽・′ README 騾壹ｊ縺ｫ襍ｷ蜍輔＠縲・蛻・ョ繝｢繧貞ｮ溯｡後＠縲∵焔蜍輔ユ繧ｹ繝医こ繝ｼ繧ｹ縺ｨ smoke 縺ｧ蜿励￠蜈･繧檎｢ｺ隱阪〒縺阪ｋ迥ｶ諷九↓縺吶ｋ縺薙→縺ｫ縺ゅｋ縲・
## Phase1 讖溯・

- Auth / Session / CSRF / RBAC 繧呈署萓帙☆繧九・- Chat Session / History / Tags API 繧呈署萓帙☆繧九・- Document Management / Upload / Version / Approve / Archive API 繧呈署萓帙☆繧九・- Worker / Job Queue / Lease / Retry 縺ｮ蝓ｺ逶､繧呈署萓帙☆繧九・- Extraction / Chunking / Embedding / Qdrant Indexing 繧呈署萓帙☆繧九・- `/api/v1/rag/search` 縺ｧ retrieval / rerank 縺ｮ遒ｺ隱榊ｰ守ｷ壹ｒ謠蝉ｾ帙☆繧九・- `/api/v1/rag/ask` 縺ｧ chat persistence縲…itation縲…onfidence縲“roundedness 繧堤｢ｺ隱阪☆繧九・- React UI 縺ｧ chat縲…itation panel縲∥dmin document/job縲‘valuation 繧堤｢ｺ隱阪☆繧九・- Evaluation minimal runner 縺ｨ fixture 繧呈署萓帙☆繧九・- MCP stdio 縺ｨ Streamable HTTP (spec 2025-06-18) 縺ｧ local-only 縺ｮ read-mostly tools/resources/prompts 繧呈署萓帙☆繧九・
## Architecture

```text
Browser
  -> React/Vite frontend
  -> FastAPI backend
     -> PostgreSQL: users, sessions, documents, jobs, chat, retrieval traces, citations, evaluations
     -> local upload storage: uploaded demo files
     -> Qdrant: document chunk vectors
     -> worker: queued jobs, ingest, evaluation
     -> MCP stdio / HTTP server: local client integration
```

Phase1 縺ｯ Docker Compose 繝ｭ繝ｼ繧ｫ繝ｫ讀懆ｨｼ迺ｰ蠅・ｒ蝓ｺ貅悶↓縺吶ｋ縲・WS deploy縲ゝerraform縲‘xternal remote MCP縲＾Auth縲＾CR縲；raphRAG縲、gentic RAG 縺ｯ Phase2 莉･髯阪・遽・峇縺ｨ縺吶ｋ縲・
## Tech Stack

| Area | Choice | Reason |
|---|---|---|
| Backend | FastAPI / Python 3.11 | 蝙倶ｻ倥″ schema縲∽ｾ晏ｭ俶ｳｨ蜈･縲＾penAPI縲・撼蜷梧悄 upload 縺ｨ縺ｮ逶ｸ諤ｧ縺後ｈ縺・・|
| ORM / Migration | SQLAlchemy 2.x / Alembic | DB 蛻ｶ邏・→ migration 螻･豁ｴ繧呈・遉ｺ縺励￣hase1 縺ｮ迥ｶ諷矩・遘ｻ繧定ｿｽ縺・ｄ縺吶￥縺吶ｋ縲・|
| Frontend | React / TypeScript / Vite | 邂｡逅・UI 縺ｨ chat UI 繧貞ｰ上＆縺丈ｿ昴■縲》ypecheck 縺ｨ build 繧・CI 縺ｧ蜀咲樟縺励ｄ縺吶￥縺吶ｋ縲・|
| RDB | PostgreSQL | session縲ヽBAC縲）ob縲〉etrieval trace縲‘valuation 繧貞宛邏・ｻ倥″縺ｧ謇ｱ縺・・|
| Vector DB | Qdrant | local compose 縺ｧ襍ｷ蜍輔＠繧・☆縺上…hunk vector 縺ｮ upsert 縺ｨ search 繧貞・髮｢縺ｧ縺阪ｋ縲・|
| Runtime | Docker Compose | Windows Docker Desktop 縺ｨ Ubuntu 縺ｮ荳｡譁ｹ縺ｧ蜷後§讒区・繧堤｢ｺ隱阪☆繧九・|
| MCP | stdio + Streamable HTTP (2025-06-18) | local-only 縺ｧ螟夜Κ蜈ｬ髢九↑縺励↓ tool/resource/prompt 繧堤｢ｺ隱阪☆繧九・|

## Security Design

- Session cookie 縺ｯ HttpOnly 縺ｫ縺励，SRF token 繧貞挨縺ｫ謇ｱ縺・・- Admin / viewer 縺ｮ RBAC 縺ｧ邂｡逅・API 縺ｨ髢ｲ隕ｧ邉ｻ蟆守ｷ壹ｒ蛻・￠繧九・- login rate limit縲《ession expiry縲，SRF pre-auth flow 繧剃ｽｿ縺・・- upload 縺ｯ諡｡蠑ｵ蟄・allowlist 縺ｨ size limit 繧剃ｽｿ縺・１R-34 譎らせ縺ｮ騾壼ｸｸ蜿悶ｊ霎ｼ縺ｿ縺ｯ PDF / DOCX / TXT / Markdown / CSV / XLSX / PPTX 繧貞ｯｾ雎｡縺ｫ縺励［acro-enabled Office files縲〕egacy `.xls` / `.ppt`縲＾CR 縺ｯ蟇ｾ雎｡螟悶→縺吶ｋ縲・- audit log 縺ｫ縺ｯ action 縺ｨ target 縺ｮ隕∫ｴ・□縺代ｒ谿九☆縲・- UI 縺ｨ MCP 縺ｯ raw token縲…redential縲｝assword_hash縲《ession縲…srf縲’ull prompt縲’ull context 繧定｡ｨ遉ｺ縺励↑縺・・- MCP 縺ｯ `MCP_LOCAL_ONLY=true` 縺ｨ stdio / Streamable HTTP 繧貞燕謠舌↓縺励？TTP 縺ｯ API key 繧貞ｿ・医↓縺吶ｋ縲Ｘrite tools 縺ｯ Phase1 縺ｧ謠蝉ｾ帙＠縺ｪ縺・・- `.env` 縺ｮ蛟､縺ｯ README 繧・docs 縺ｫ霆｢險倥＠縺ｪ縺・ょｿ・ｦ√↑螟画焚蜷阪・ `.env.example` 繧定ｦ九ｋ縲・
## Hallucination Mitigation

- 蝗樒ｭ斐・ retrieval result 縺ｫ蝓ｺ縺･縺上・- citation 縺ｯ selected chunk 縺ｨ retrieval run 縺ｫ邏舌▼縺代ｋ縲・- confidence 縺ｨ groundedness 繧剃ｿ晏ｭ倥＠縲ゞI 縺ｧ遒ｺ隱阪☆繧九・- no-context 縺ｮ雉ｪ蝠上・騾壼ｸｸ蝗樒ｭ斐→蛻・￠縺ｦ謇ｱ縺・・- evaluation fixture 縺ｧ expected keywords 縺ｨ citation coverage 繧堤｢ｺ隱阪☆繧九・- CI 縺ｯ fake adapter 繧剃ｽｿ縺・∝､夜Κ API 繧・､ｧ縺阪↑ model download 繧帝壼ｸｸ遒ｺ隱阪↓蜷ｫ繧√↑縺・・
## Citation / Confidence

`/api/v1/rag/ask` 縺ｯ user message縲∥ssistant message縲〉etrieval_run縲〉etrieval_run_items縲…itations 繧貞酔縺俶ｵ√ｌ縺ｧ菫晏ｭ倥☆繧九Ｄitation panel 縺ｯ source label縲｝age縲《ection縲《nippet preview 繧定｡ｨ遉ｺ縺吶ｋ縲Ｄonfidence 縺ｯ `High`縲～Medium`縲～Low` 縺ｮ label 縺ｨ score 繧定ｿ斐☆縲・
## Evaluation

Phase1 縺ｮ evaluation 縺ｯ `backend/app/evaluation/fixtures/phase1_smoke.json` 繧貞・蜿｣縺ｫ縺吶ｋ縲Ｅefault dataset 縺ｯ `phase1_smoke` 縺ｧ縲《eed 譁・嶌縺ｨ sample questions 縺ｫ蜷医ｏ縺帙◆雉ｪ蝠上ｒ蜷ｫ繧縲・I 縺ｨ demo 縺ｧ縺ｯ fake generation / fake rerank 繧剃ｽｿ縺医ｋ縺溘ａ縲∝､夜Κ LLM 繧貞ｿ・医↓縺励↑縺・・
## MCP Support

MCP server 縺ｯ local-only 縺ｧ縲《tdio 縺ｨ Streamable HTTP (spec 2025-06-18) 繧呈署萓帙☆繧九ゅョ繝輔か繝ｫ繝医・ stdio 縺ｧ縺ゅｋ縲・
```bash
cd backend
python -m app.mcp.server --transport stdio
```

HTTP transport 縺ｯ `MCP_TRANSPORT=http` 縺ｨ `MCP_HTTP_API_KEY` 繧定ｨｭ螳壹＠縺・backend 縺ｧ `POST /mcp` 縺ｨ縺励※譛牙柑縺ｫ縺ｪ繧九・ify 縺ｪ縺ｩ縺九ｉ縺ｮ謗･邯壽焔鬆・・ [docs/MCP_HTTP_DIFY.md](docs/MCP_HTTP_DIFY.md) 繧貞盾辣ｧ縺吶ｋ縲・
荳ｻ縺ｪ tools 縺ｯ谺｡縺ｮ騾壹ｊ縺ｧ縺ゅｋ縲・
- `rag_search`
- `rag_ask`
- `list_documents`
- `get_document_status`
- `get_job_status`
- `list_evaluation_runs`
- `get_evaluation_result`

Claude Desktop / Cursor / Codex 縺ｪ縺ｩ縺ｮ stdio local MCP client 縺ｫ縺ｯ縲《ecret 繧貞性繧√★縺ｫ command 縺ｨ working directory 縺縺代ｒ險ｭ螳壹☆繧九ゆｾ九・ [docs/demo/mcp_demo.md](docs/demo/mcp_demo.md) 縺ｫ鄂ｮ縺上・
## Requirements

- Docker Desktop 縺ｾ縺溘・ Docker Engine + Docker Compose plugin
- Git
- Windows: Windows 11 + Docker Desktop Linux containers
- Ubuntu: Ubuntu 24.04.4 LTS + Docker Engine
- Optional local development: Python 3.11縲「v縲¨ode.js 20

## Windows Docker Desktop

PowerShell 繧帝幕縺阪´inux containers 縺瑚ｵｷ蜍輔＠縺ｦ縺・ｋ縺薙→繧堤｢ｺ隱阪☆繧九・
```powershell
git clone https://github.com/M4kuq/RAGProject.git
cd RAGProject
Copy-Item .env.example .env
docker compose config
docker compose up --build
```

蛻･ PowerShell 縺ｧ遒ｺ隱阪☆繧九・
```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
.\scripts\smoke_phase1.ps1
```

## Ubuntu 24.04.4 LTS

Docker Engine 縺ｨ Compose plugin 繧剃ｽｿ縺・・
```bash
git clone https://github.com/M4kuq/RAGProject.git
cd RAGProject
cp .env.example .env
docker compose config
docker compose up --build
```

蛻･ shell 縺ｧ遒ｺ隱阪☆繧九・
```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
sh scripts/smoke_phase1.sh
```

## Quick Start

1. `.env.example` 繧・`.env` 縺ｫ繧ｳ繝斐・縺吶ｋ縲・2. `docker compose up --build` 繧貞ｮ溯｡後☆繧九・3. `http://localhost:5173` 繧帝幕縺上・4. local demo account 縺ｧ login 縺吶ｋ縲Ａadmin@example.com` / `password` 縺ｯ繝ｭ繝ｼ繧ｫ繝ｫ繝・Δ逕ｨ縺ｮ dummy credential 縺ｧ縺ゅｋ縲・5. sample questions 縺ｯ [docs/demo/sample_questions.md](docs/demo/sample_questions.md) 繧剃ｽｿ縺・・
## Migration / Seed

Compose 襍ｷ蜍墓凾縺ｫ `migrate` 縺ｨ `seed` service 縺悟ｮ溯｡後＆繧後ｋ縲ょ句挨螳溯｡後☆繧句ｴ蜷医・谺｡繧剃ｽｿ縺・・
```bash
docker compose run --rm migrate
docker compose run --rm seed
```

seed 縺ｯ idempotent 縺ｫ菴懊ｋ縲Ｂdmin / viewer縲「ser_settings縲《ystem_settings縲‥emo documents縲｛ld/new version pair縲《ample question metadata縲‘valuation fixture reference 繧呈兜蜈･縺吶ｋ縲・
## Start / Stop

```bash
docker compose up --build
```

蛛懈ｭ｢縺縺代↑繧画ｬ｡繧剃ｽｿ縺・・
```bash
docker compose stop
```

蛻晄悄迥ｶ諷九↓謌ｻ縺吝燕縺ｫ縲∵ｬ｡縺ｮ豕ｨ諢上ｒ遒ｺ隱阪☆繧九・
> docker compose down -v deletes local database, qdrant data, and uploaded files.

縺薙・繧ｳ繝槭Φ繝峨・ local volume 繧呈ｶ医☆縲ょｿ・ｦ√↑ demo data 繧・､懆ｨｼ邨先棡繧呈ｮ九＠縺溘＞蝣ｴ蜷医・菴ｿ繧上↑縺・・
## Local Validation Commands

Windows:

```powershell
.\scripts\test.ps1
.\scripts\test.ps1 -Smoke
.\scripts\smoke_phase1.ps1
.\scripts\smoke_phase1.ps1 -Deep
.\scripts\smoke_phase3_graph_rag.ps1
```

Ubuntu:

```bash
sh scripts/test.sh
sh scripts/test.sh --smoke
sh scripts/smoke_phase1.sh
sh scripts/smoke_phase1.sh --deep
sh scripts/smoke_phase3_graph_rag.sh
```

Backend:

```bash
cd backend
uv run --extra dev ruff format --check .
uv run --extra dev ruff check .
uv run --extra dev mypy .
uv run --extra dev pytest
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Compose:

```bash
docker compose config
docker compose -f docker-compose.ci.yml config
docker compose -f docker-compose.ci.yml run --rm backend-test
docker compose -f docker-compose.ci.yml run --rm frontend-test
docker compose -f docker-compose.ci.yml run --rm --no-deps smoke
```

Retrieval evaluation smoke:

```powershell
.\scripts\run_retrieval_eval_smoke.ps1 -Dataset phase2_strategy_smoke -Strategies dense,hybrid,agentic_router -ThresholdMode warn
```

```bash
sh scripts/run_retrieval_eval_smoke.sh
```

## CI/CD

GitHub Actions 縺ｯ谺｡縺ｮ workflow 繧剃ｽｿ縺・・
| workflow | Target | Local equivalent |
|---|---|---|
| Backend CI | ruff format check縲〉uff check縲［ypy縲｝ytest | `cd backend && uv run --extra dev pytest` 縺ｪ縺ｩ |
| Frontend CI | npm install縲〕int縲》ypecheck縲〃itest縲｜uild | `cd frontend && npm run build` 縺ｪ縺ｩ |
| Docker CI | compose config縲（mage build | `docker compose -f docker-compose.ci.yml build ...` |
| Compose Smoke | migration縲《eed縲｜ackend readiness縲『orker health縲＿drant縲’rontend artifact | `scripts/test.* -Smoke` |
| Retrieval Evaluation Smoke | manual/scheduled deterministic strategy evaluation | `scripts/run_retrieval_eval_smoke.*` |
| Retrieval Model Experiment | local opt-in embedding/reranker comparison | `scripts/run_retrieval_model_experiment.*` |

Retrieval Evaluation Smoke 縺ｯ workflow_dispatch / optional schedule 縺ｧ螳溯｡後☆繧・real retrieval smoke 縺ｧ縺吶１ostgreSQL縲＿drant縲（ndexed demo documents縲∝ｰ丞梛 local embedding model cache 繧剃ｽｿ縺・∥nswer generation 縺ｯ螳溯｡後＠縺ｾ縺帙ｓ縲Ｇake embedding / fake reranker / fake evaluator 縺ｫ縺ｯ fallback 縺帙★縲〕ocal model/cache 縺御ｸ崎ｶｳ縺吶ｋ蝣ｴ蜷医・ safe artifact 縺ｧ `blocked` 縺ｨ縺励※蝣ｱ蜻翫＠縲・壼ｸｸ PR CI 縺ｮ蠢・・gate 縺ｫ縺ｯ縺励∪縺帙ｓ縲・
Optional trace export:

```text
TRACE_EXPORT_ENABLED=false
TRACE_EXPORT_PROVIDER=none
```

LangSmith export is opt-in only. Normal CI and local smoke runs do not require
LangSmith secrets, and exported payloads are minimized/redacted summaries rather
than raw prompts, full context, raw chunk text, answers, PII, tokens, or
credentials. See `docs/phase2/langsmith_optional_adapter.md`.

SentenceTransformers experiment harness:

```powershell
.\scripts\run_retrieval_model_experiment.ps1 -Mode dry-run
.\scripts\run_retrieval_model_experiment.ps1 -Mode local -DownloadPolicy if-cached
```

```bash
sh scripts/run_retrieval_model_experiment.sh
```

Experiments are local opt-in only. Normal CI does not download models, require a
GPU, or require external API keys. Dry-run validates the manifest, model
registry, availability status, and safe artifact/report shape. Local mode uses
cached public SentenceTransformers models by default and writes only aggregate
metrics and reason codes. See
`docs/phase2/sentence_transformers_experiment_harness.md`.

## Demo / Test Docs

- [5min demo](docs/demo/5min_demo.md)
- [local NVIDIA Build API generation](docs/demo/nvidia_build_api.md)
- [UI demo](docs/demo/ui_demo.md)
- [CLI demo](docs/demo/cli_demo.md)
- [MCP demo](docs/demo/mcp_demo.md)
- [MCP advanced RAG tools](docs/phase2/mcp_advanced_rag_tools.md)
- [GraphRAG final README](docs/phase3/graph_rag_final_readme.md)
- [GraphRAG demo scenario](docs/phase3/graph_rag_demo_scenario.md)
- [GraphRAG manual test cases](docs/phase3/graph_rag_manual_test_cases.md)
- [GraphRAG acceptance checklist](docs/phase3/graph_rag_acceptance_checklist.md)
- [GraphRAG known limitations](docs/phase3/graph_rag_known_limitations.md)
- [sample questions](docs/demo/sample_questions.md)
- [demo data](docs/demo/demo_data.md)
- [manual test cases](docs/test-cases/phase1_manual_test_cases.md)
- [acceptance checklist](docs/test-cases/phase1_acceptance_checklist.md)
- [troubleshooting](docs/troubleshooting.md)

## Troubleshooting

隧ｳ邏ｰ縺ｯ [docs/troubleshooting.md](docs/troubleshooting.md) 縺ｫ鄂ｮ縺上ゅｈ縺丈ｽｿ縺・｢ｺ隱阪・谺｡縺ｧ縺ゅｋ縲・
```bash
docker compose ps
docker compose logs backend
docker compose logs worker
docker compose -f docker-compose.ci.yml config
```

## Known Limitations

- Phase1 縺ｯ local Docker Compose 讀懆ｨｼ蜷代￠縺ｧ縺ゅｊ縲…loud deploy 縺ｯ謇ｱ繧上↑縺・・- MCP 縺ｯ local-only 縺ｧ縺ゅｊ縲？TTP 縺ｯ localhost bind 縺ｨ API key 蜑肴署縺ｧ縺ゅｋ縲４SE streaming縲《ession management縲＾Auth縲‘xternal remote MCP 縺ｯ謇ｱ繧上↑縺・・- OCR and multimodal input remain future work. Text GraphRAG is covered by the
  Phase3 PR-54 docs and remains opt-in for local demos.
- CI 縺ｮ騾壼ｸｸ遒ｺ隱阪・ fake adapter 繧貞━蜈医☆繧九・- PDF / DOCX 縺ｮ螟ｧ縺阪↑ demo fixture 縺ｯ repository 縺ｫ霑ｽ蜉縺励↑縺・ょｿ・ｦ√↑蝣ｴ蜷医・謇句虚 upload 謇矩・〒遒ｺ隱阪☆繧九・- worker 縺ｮ驥阪＞螳溘ず繝ｧ繝也｢ｺ隱阪・迺ｰ蠅・ｷｮ縺悟・繧九◆繧√∵怙邨ょ女縺大・繧後〒縺ｯ manual test 縺ｨ optional smoke 縺ｫ蛻・￠繧九・
## Phase2 Roadmap

- cloud deploy 縺ｨ secrets management 繧定ｨｭ險医☆繧九・- real embedding / reranker / generator 縺ｮ讀懆ｨｼ profile 繧定ｿｽ蜉縺吶ｋ縲・- OCR 縺ｨ large document handling 繧呈僑蠑ｵ縺吶ｋ縲・- evaluation metrics 縺ｨ regression dashboard 繧呈僑蠑ｵ縺吶ｋ縲・- external remote MCP縲＾Auth縲…lient-specific integration 繧呈､懆ｨ弱☆繧九・
