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

RAGProject は、文書アップロード、抽出、chunking、embedding、Qdrant index、retrieval、rerank、回答生成、citation、confidence、evaluation、MCP stdio / Streamable HTTP server までを Docker Compose のローカル環境で確認する Phase1 RAG ポートフォリオである。

Phase1 の目的は、クラウド公開ではなく、第三者が README 通りに起動し、5分デモを実行し、手動テストケースと smoke で受け入れ確認できる状態にすることにある。

## Phase1 機能

- Auth / Session / CSRF / RBAC を提供する。
- Chat Session / History / Tags API を提供する。
- Document Management / Upload / Version / Approve / Archive API を提供する。
- Worker / Job Queue / Lease / Retry の基盤を提供する。
- Extraction / Chunking / Embedding / Qdrant Indexing を提供する。
- `/api/v1/rag/search` で retrieval / rerank の確認導線を提供する。
- `/api/v1/rag/ask` で chat persistence、citation、confidence、groundedness を確認する。
- React UI で chat、citation panel、admin document/job、evaluation を確認する。
- Evaluation minimal runner と fixture を提供する。
- MCP stdio と Streamable HTTP (spec 2025-06-18) で local-only の read-mostly tools/resources/prompts を提供する。

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

Phase1 は Docker Compose ローカル検証環境を基準にする。AWS deploy、Terraform、external remote MCP、OAuth、OCR、GraphRAG、Agentic RAG は Phase2 以降の範囲とする。

## Tech Stack

| Area | Choice | Reason |
|---|---|---|
| Backend | FastAPI / Python 3.11 | 型付き schema、依存注入、OpenAPI、非同期 upload との相性がよい。 |
| ORM / Migration | SQLAlchemy 2.x / Alembic | DB 制約と migration 履歴を明示し、Phase1 の状態遷移を追いやすくする。 |
| Frontend | React / TypeScript / Vite | 管理 UI と chat UI を小さく保ち、typecheck と build を CI で再現しやすくする。 |
| RDB | PostgreSQL | session、RBAC、job、retrieval trace、evaluation を制約付きで扱う。 |
| Vector DB | Qdrant | local compose で起動しやすく、chunk vector の upsert と search を分離できる。 |
| Runtime | Docker Compose | Windows Docker Desktop と Ubuntu の両方で同じ構成を確認する。 |
| MCP | stdio + Streamable HTTP (2025-06-18) | local-only で外部公開なしに tool/resource/prompt を確認する。 |

## Security Design

- Session cookie は HttpOnly にし、CSRF token を別に扱う。
- Admin / viewer の RBAC で管理 API と閲覧系導線を分ける。
- login rate limit、session expiry、CSRF pre-auth flow を使う。
- upload は拡張子 allowlist と size limit を使う。PR-34 時点の通常取り込みは PDF / DOCX / TXT / Markdown / CSV / XLSX / PPTX を対象にし、macro-enabled Office files、legacy `.xls` / `.ppt`、OCR は対象外とする。
- audit log には action と target の要約だけを残す。
- UI と MCP は raw token、credential、password_hash、session、csrf、full prompt、full context を表示しない。
- MCP は `MCP_LOCAL_ONLY=true` と stdio / Streamable HTTP を前提にし、HTTP は API key を必須にする。write tools は Phase1 で提供しない。
- `.env` の値は README や docs に転記しない。必要な変数名は `.env.example` を見る。

## Hallucination Mitigation

- 回答は retrieval result に基づく。
- citation は selected chunk と retrieval run に紐づける。
- confidence と groundedness を保存し、UI で確認する。
- no-context の質問は通常回答と分けて扱う。
- evaluation fixture で expected keywords と citation coverage を確認する。
- CI は fake adapter を使い、外部 API や大きな model download を通常確認に含めない。

## Citation / Confidence

`/api/v1/rag/ask` は user message、assistant message、retrieval_run、retrieval_run_items、citations を同じ流れで保存する。citation panel は source label、page、section、snippet preview を表示する。confidence は `High`、`Medium`、`Low` の label と score を返す。

## Evaluation

Phase1 の evaluation は `backend/app/evaluation/fixtures/phase1_smoke.json` を入口にする。default dataset は `phase1_smoke` で、seed 文書と sample questions に合わせた質問を含む。CI と demo では fake generation / fake rerank を使えるため、外部 LLM を必須にしない。

## MCP Support

MCP server は local-only で、stdio と Streamable HTTP (spec 2025-06-18) を提供する。デフォルトは stdio である。

```bash
cd backend
python -m app.mcp.server --transport stdio
```

HTTP transport は `MCP_TRANSPORT=http` と `MCP_HTTP_API_KEY` を設定した backend で `POST /mcp` として有効になる。Dify などからの接続手順は [docs/MCP_HTTP_DIFY.md](docs/MCP_HTTP_DIFY.md) を参照する。

主な tools は次の通りである。

- `rag_search`
- `rag_ask`
- `list_documents`
- `get_document_status`
- `get_job_status`
- `list_evaluation_runs`
- `get_evaluation_result`

Claude Desktop / Cursor / Codex などの stdio local MCP client には、secret を含めずに command と working directory だけを設定する。例は [docs/demo/mcp_demo.md](docs/demo/mcp_demo.md) に置く。

## Requirements

- Docker Desktop または Docker Engine + Docker Compose plugin
- Git
- Windows: Windows 11 + Docker Desktop Linux containers
- Ubuntu: Ubuntu 24.04.4 LTS + Docker Engine
- Optional local development: Python 3.11、uv、Node.js 20

## Windows Docker Desktop

PowerShell を開き、Linux containers が起動していることを確認する。

```powershell
git clone https://github.com/M4kuq/RAGProject.git
cd RAGProject
Copy-Item .env.example .env
docker compose config
docker compose up --build
```

別 PowerShell で確認する。

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
.\scripts\smoke_phase1.ps1
```

## Ubuntu 24.04.4 LTS

Docker Engine と Compose plugin を使う。

```bash
git clone https://github.com/M4kuq/RAGProject.git
cd RAGProject
cp .env.example .env
docker compose config
docker compose up --build
```

別 shell で確認する。

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
sh scripts/smoke_phase1.sh
```

## Quick Start

1. `.env.example` を `.env` にコピーする。
2. `docker compose up --build` を実行する。
3. `http://localhost:5173` を開く。
4. local demo account で login する。`admin@example.com` / `password` はローカルデモ用の dummy credential である。
5. sample questions は [docs/demo/sample_questions.md](docs/demo/sample_questions.md) を使う。

## Migration / Seed

Compose 起動時に `migrate` と `seed` service が実行される。個別実行する場合は次を使う。

```bash
docker compose run --rm migrate
docker compose run --rm seed
```

seed は idempotent に作る。admin / viewer、user_settings、system_settings、demo documents、old/new version pair、sample question metadata、evaluation fixture reference を投入する。

## Start / Stop

```bash
docker compose up --build
```

停止だけなら次を使う。

```bash
docker compose stop
```

初期状態に戻す前に、次の注意を確認する。

> docker compose down -v deletes local database, qdrant data, and uploaded files.

このコマンドは local volume を消す。必要な demo data や検証結果を残したい場合は使わない。

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

GitHub Actions は次の workflow を使う。

| workflow | Target | Local equivalent |
|---|---|---|
| Backend CI | ruff format check、ruff check、mypy、pytest | `cd backend && uv run --extra dev pytest` など |
| Frontend CI | npm install、lint、typecheck、Vitest、build | `cd frontend && npm run build` など |
| Docker CI | compose config、image build | `docker compose -f docker-compose.ci.yml build ...` |
| Compose Smoke | migration、seed、backend readiness、worker health、Qdrant、frontend artifact | `scripts/test.* -Smoke` |
| Retrieval Evaluation Smoke | manual/scheduled deterministic strategy evaluation | `scripts/run_retrieval_eval_smoke.*` |
| Retrieval Model Experiment | local opt-in embedding/reranker comparison | `scripts/run_retrieval_model_experiment.*` |

Retrieval Evaluation Smoke は workflow_dispatch / optional schedule で実行する real retrieval smoke です。PostgreSQL、Qdrant、indexed demo documents、小型 local embedding model cache を使い、answer generation は実行しません。fake embedding / fake reranker / fake evaluator には fallback せず、local model/cache が不足する場合は safe artifact で `blocked` として報告し、通常 PR CI の必須 gate にはしません。

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

詳細は [docs/troubleshooting.md](docs/troubleshooting.md) に置く。よく使う確認は次である。

```bash
docker compose ps
docker compose logs backend
docker compose logs worker
docker compose -f docker-compose.ci.yml config
```

## Known Limitations

- Phase1 は local Docker Compose 検証向けであり、cloud deploy は扱わない。
- MCP は local-only であり、HTTP は localhost bind と API key 前提である。SSE streaming、session management、OAuth、external remote MCP は扱わない。
- OCR and multimodal input remain future work. Text GraphRAG is covered by the
  Phase3 PR-54 docs and remains opt-in for local demos.
- CI の通常確認は fake adapter を優先する。
- PDF / DOCX の大きな demo fixture は repository に追加しない。必要な場合は手動 upload 手順で確認する。
- worker の重い実ジョブ確認は環境差が出るため、最終受け入れでは manual test と optional smoke に分ける。

## Phase2 Roadmap

- cloud deploy と secrets management を設計する。
- real embedding / reranker / generator の検証 profile を追加する。
- OCR と large document handling を拡張する。
- evaluation metrics と regression dashboard を拡張する。
- external remote MCP、OAuth、client-specific integration を検討する。
