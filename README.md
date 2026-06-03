# RAGProject

## Phase2.5 Final Handoff

Phase2.5 final demo, Context Engineering acceptance, local Kubernetes demo, known limitations, and Phase3/deploy handoff material lives under [`docs/phase2/phase2_5_readme.md`](docs/phase2/phase2_5_readme.md).

Start with:

- [`docs/phase2/phase2_5_readme.md`](docs/phase2/phase2_5_readme.md)
- [`docs/phase2/phase2_5_demo_scenario.md`](docs/phase2/phase2_5_demo_scenario.md)
- [`docs/phase2/context_engineering_readme.md`](docs/phase2/context_engineering_readme.md)
- [`docs/phase2/context_engineering_manual_test_cases.md`](docs/phase2/context_engineering_manual_test_cases.md)
- [`docs/phase2/context_engineering_acceptance_checklist.md`](docs/phase2/context_engineering_acceptance_checklist.md)
- [`docs/phase2/context_engineering_known_limitations.md`](docs/phase2/context_engineering_known_limitations.md)
- [`docs/phase2/kubernetes_baseline.md`](docs/phase2/kubernetes_baseline.md)
- [`docs/phase2/phase3_handoff.md`](docs/phase2/phase3_handoff.md)
- [`docs/phase2/deploy_aws_handoff.md`](docs/phase2/deploy_aws_handoff.md)

Safe Phase2.5 smoke:

```powershell
scripts\smoke_phase2_5.ps1
scripts\smoke_phase2_5.ps1 -K8sDryRun
```

```sh
sh scripts/smoke_phase2_5.sh
sh scripts/smoke_phase2_5.sh --k8s-dry-run
```

The Phase2.5 smoke does not run destructive cleanup, does not print `.env` values, does not print kubeconfig, and does not require external API keys, external exports, GPU, or mandatory model downloads.

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
[`docs/phase2/kubernetes_local_baseline.md`](docs/phase2/kubernetes_local_baseline.md) and the Phase2.5 entrypoint [`docs/phase2/kubernetes_baseline.md`](docs/phase2/kubernetes_baseline.md).

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

RAGProject は、文書アップロード、抽出、chunking、embedding、Qdrant index、retrieval、rerank、回答生成、citation、confidence、evaluation、MCP stdio server までを Docker Compose のローカル環境で確認する Phase1 RAG ポートフォリオである。

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
- MCP stdio server で local-only の read-mostly tools/resources/prompts を提供する。

## Architecture

```text
Browser
  -> React/Vite frontend
  -> FastAPI backend
     -> PostgreSQL: users, sessions, documents, jobs, chat, retrieval traces, citations, evaluations
     -> local upload storage: uploaded demo files
     -> Qdrant: document chunk vectors
     -> worker: queued jobs, ingest, evaluation
     -> MCP stdio server: local client integration
```

Phase1 は Docker Compose ローカル検証環境を基準にする。AWS deploy、Terraform、remote MCP、OAuth、OCR、GraphRAG、Agentic RAG は Phase2 以降の範囲とする。

## Tech Stack

| Area | Choice | Reason |
|---|---|---|
| Backend | FastAPI / Python 3.11 | 型付き schema、依存注入、OpenAPI、非同期 upload との相性がよい。 |
| ORM / Migration | SQLAlchemy 2.x / Alembic | DB 制約と migration 履歴を明示し、Phase1 の状態遷移を追いやすくする。 |
| Frontend | React / TypeScript / Vite | 管理 UI と chat UI を小さく保ち、typecheck と build を CI で再現しやすくする。 |
| RDB | PostgreSQL | session、RBAC、job、retrieval trace、evaluation を制約付きで扱う。 |
| Vector DB | Qdrant | local compose で起動しやすく、chunk vector の upsert と search を分離できる。 |
| Runtime | Docker Compose | Windows Docker Desktop と Ubuntu の両方で同じ構成を確認する。 |
| MCP | stdio server | local-only で外部公開なしに tool/resource/prompt を確認する。 |

## Security Design

- Session cookie は HttpOnly にし、CSRF token を別に扱う。
- Admin / viewer の RBAC で管理 API と閲覧系導線を分ける。
- login rate limit、session expiry、CSRF pre-auth flow を使う。
- upload は拡張子 allowlist と size limit を使う。PR-34 時点の通常取り込みは PDF / DOCX / TXT / Markdown / CSV / XLSX / PPTX を対象にし、macro-enabled Office files、legacy `.xls` / `.ppt`、OCR は対象外とする。
- audit log には action と target の要約だけを残す。
- UI と MCP は raw token、credential、password_hash、session、csrf、full prompt、full context を表示しない。
- MCP は `MCP_LOCAL_ONLY=true` と stdio を前提にし、write tools を Phase1 で提供しない。
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

MCP server は local-only / stdio で起動する。

```bash
cd backend
python -m app.mcp.server --transport stdio
```

主な tools は次の通りである。

- `rag_search`
- `rag_ask`
- `rag_ask_auto`
- `list_documents`
- `get_document_status`
- `get_job_status`
- `list_evaluation_runs`
- `get_evaluation_result`

Claude Desktop / Cursor / Codex などの local MCP client には、secret を含めずに command と working directory だけを設定する。例は [docs/demo/mcp_demo.md](docs/demo/mcp_demo.md) に置く。

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
