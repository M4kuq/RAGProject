# RAGProject

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
- upload は拡張子 allowlist と size limit を使う。
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
```

Ubuntu:

```bash
sh scripts/test.sh
sh scripts/test.sh --smoke
sh scripts/smoke_phase1.sh
sh scripts/smoke_phase1.sh --deep
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

Retrieval Evaluation Smoke は workflow_dispatch / optional schedule で実行する real retrieval smoke です。PostgreSQL、Qdrant、indexed demo documents、小型 local embedding model cache を使い、answer generation は実行しません。fake embedding / fake reranker / fake evaluator には fallback せず、local model/cache が不足する場合は safe artifact で `blocked` として報告し、通常 PR CI の必須 gate にはしません。

## Demo / Test Docs

- [5min demo](docs/demo/5min_demo.md)
- [UI demo](docs/demo/ui_demo.md)
- [CLI demo](docs/demo/cli_demo.md)
- [MCP demo](docs/demo/mcp_demo.md)
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
- MCP は stdio / local-only であり、remote MCP は扱わない。
- OCR、GraphRAG、Agentic RAG は扱わない。
- CI の通常確認は fake adapter を優先する。
- PDF / DOCX の大きな demo fixture は repository に追加しない。必要な場合は手動 upload 手順で確認する。
- worker の重い実ジョブ確認は環境差が出るため、最終受け入れでは manual test と optional smoke に分ける。

## Phase2 Roadmap

- cloud deploy と secrets management を設計する。
- real embedding / reranker / generator の検証 profile を追加する。
- OCR と large document handling を拡張する。
- evaluation metrics と regression dashboard を拡張する。
- remote MCP、OAuth、client-specific integration を検討する。
