# RAGProject

文書アップロードから検索、回答生成、citation、評価、監査ログまでを Docker Compose で再現できる RAG システムです。Phase1 では、ローカル検証環境で主要機能を一通り確認できる構成を目標にします。

## Phase1 の前提

- Backend: Python 3.11 / FastAPI / SQLAlchemy 2.x / Pydantic v2 / Alembic
- Frontend: React / TypeScript / Vite / React Router / TanStack Query / React Hook Form
- Data: PostgreSQL / Qdrant / local file storage
- Runtime: Docker Desktop または Docker Compose
- CI/CD: GitHub Actions で lint、format check、type check、test、Docker build、compose smoke を実行予定

## Docker Compose 構成

現時点では、ローカル検証用の Docker/Compose 土台を追加しています。

- `frontend`: Vite dev server。ブラウザから `http://localhost:5173` でアクセスします。
- `backend`: FastAPI。`/health` は liveness、`/ready` は DB 接続を含む readiness です。現時点の `/ready` は DB-only で、Qdrant / LLM を含む RAG readiness は後続実装で追加します。
- `worker`: jobs table polling worker。migration と seed 完了後に起動します。
- `postgres`: Phase1 の RDB。
- `qdrant`: vector store。CI では fake adapter 前提で重いモデル download を必須にしません。
- `ollama`: ローカル LLM 実行候補。CI の最小 compose では起動対象外です。

永続 volume は `ragproject_postgres_data`、`ragproject_qdrant_data`、`ragproject_ollama_data`、`ragproject_upload_storage` です。CI 用 compose は `ragproject_ci_*` の別 volume を使います。

`docker-compose.ci.yml` は CI smoke 専用のスタンドアロン構成です。Ollama を起動せず、fake adapter 前提で Docker build、migration、seed、backend readiness、frontend build artifact、worker 起動を確認する入口にしています。worker の実ジョブ投入・完了確認は Worker / Job 実装で追加します。

## Windows Docker Desktop

PowerShell から実行します。

```powershell
Copy-Item .env.example .env
.\scripts\dev.ps1
```

起動後の確認例です。

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
```

テストは次の入口から実行します。

```powershell
.\scripts\test.ps1
```

Compose smoke まで実行する場合は次を使います。

```powershell
.\scripts\test.ps1 -Smoke
```

## Ubuntu 24.04.4 LTS

shell から実行します。

```bash
cp .env.example .env
sh scripts/dev.sh
```

起動後の確認例です。

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
```

テストは次の入口から実行します。

```bash
sh scripts/test.sh
```

Compose smoke まで実行する場合は次を使います。

```bash
sh scripts/test.sh --smoke
```

## GitHub Actions CI/CD

Phase1 の CI/CD 導線として GitHub Actions workflow を追加しています。

| workflow | 主な対象 | pull_request | main push | 手動実行 |
| --- | --- | --- | --- | --- |
| `Backend CI` | Ruff format check / Ruff lint / mypy / pytest | backend / scripts / workflow 変更時 | backend / scripts / workflow 変更時 | 可 |
| `Frontend CI` | npm install / lint / typecheck / Vitest / production build | frontend / workflow 変更時 | frontend / workflow 変更時 | 可 |
| `Docker CI` | CI compose config / backend・worker・frontend image build | Dockerfile / compose / dependency manifest・lockfile / `.dockerignore` / workflow 変更時 | 常時 | 可 |
| `Compose Smoke` | CI compose build / backend-test / frontend-test / backend readiness / worker health / qdrant / postgres / frontend build artifact | backend / frontend / `docker-compose.ci.yml` / Dockerfile / workflow 変更時 | 常時 | 可 |

pull_request イベントでは軽量な backend / frontend check を優先し、Docker build と compose smoke は関連ファイル変更時に限定しています。`main` push では Docker build と compose smoke まで実行します。

`Backend CI` は host runner 上の unit-level check とし、DB 統合確認は `Compose Smoke` に寄せます。`Compose Smoke` は `USE_FAKE_LLM=true`、fake model 名、PostgreSQL、Qdrant、backend、worker、frontend build artifact 確認を前提にしています。外部 LLM / embedding / reranker の本番 API 利用や、Ollama model pull、`BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3` の重い model download は必須にしません。通常CIは GitHub Actions secrets や `.env` を必要としません。

`Docker CI` は `main` push と手動実行時に、安全な範囲の rendered compose config を短期 artifact として保存します。`.env`、secret、DB dump、prompt全文、chunk本文、PII は artifact 対象にしません。

### ローカルでCI相当を再現する

Docker / Compose 系は Windows PowerShell と Ubuntu shell のどちらでも次の scripts を入口にします。

```powershell
.\scripts\test.ps1
.\scripts\test.ps1 -Smoke
```

```bash
sh scripts/test.sh
sh scripts/test.sh --smoke
```

個別に確認する場合は次を実行します。

```bash
docker compose -f docker-compose.ci.yml config
docker compose -f docker-compose.ci.yml build backend worker frontend-build backend-test frontend-test smoke
docker compose -f docker-compose.ci.yml run --rm backend-test
docker compose -f docker-compose.ci.yml run --rm frontend-test
docker compose -f docker-compose.ci.yml run --rm frontend-build
docker compose -f docker-compose.ci.yml up -d backend worker
docker compose -f docker-compose.ci.yml run --rm --no-deps smoke
```

繰り返し実行で状態が残った場合は、対象 volume を削除する操作であることを理解したうえで次を実行します。

```bash
docker compose -f docker-compose.ci.yml down --volumes --remove-orphans
```

backend CI の個別コマンドは次です。

```bash
cd backend
uv run --extra dev ruff format --check .
uv run --extra dev ruff check .
uv run --extra dev mypy .
uv run --extra dev pytest
```

frontend CI の個別コマンドは次です。

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Windows Docker Desktop では Linux container engine が起動していることを確認してください。Ubuntu 24.04.4 LTS では Docker Engine と Docker Compose plugin を利用します。

### CI troubleshooting

- cache が壊れた場合: GitHub Actions の該当 workflow を再実行し、それでも再現する場合は Actions cache を削除して再実行します。uv / npm / Docker layer cache は lockfile または `pyproject.toml` を key に含めます。
- compose smoke が race する場合: `/health` ではなく `/ready` と compose healthcheck を確認します。現時点の `/ready` は DB-only readiness で、Qdrant / RAG model readiness は後続実装で拡張します。
- Docker daemon が起動していない場合: Windows では Docker Desktop、Ubuntu では Docker service を起動してから `docker compose -f docker-compose.ci.yml config` を実行します。
- `/health` と readiness の違い: `/health` は process liveness です。DB 接続を含む起動判定は `/ready` を使います。
- secrets や `.env`: 通常CIに secrets や `.env` を入れません。外部API key、cookie、private key、DB dump、prompt全文、chunk本文、PII を log / artifact / cache に含めない方針です。

### CI 既知課題

- `backend/uv.lock` は未作成です。backend CI は `pyproject.toml` の version range から解決するため、完全固定の dependency reproducibility は後続実装で `uv.lock` と Dockerfile の frozen install 化により対応します。なお、`passlib[bcrypt]` と新しい `bcrypt` の既知互換性問題で seed が失敗するため、現時点では `bcrypt>=4.0.1,<4.1.0` の制約だけ先に明示しています。
- 2026-05-01 時点の `npm audit` では frontend 依存に moderate 5件が報告されています。現時点では CI 基盤差分に限定し、破壊的更新を伴う `npm audit fix --force` は実行していません。

## 開発基盤の現在地

これまでに、以下を追加しています。

- backend の FastAPI / uv / pytest / ruff / mypy 用雛形
- frontend の Vite / React / TypeScript / Vitest 用雛形
- `.env.example`
- Windows / Ubuntu 共通の `scripts/`
- `.editorconfig` / `.gitattributes`
- 5分デモ手順とサンプル質問集
- backend / worker / frontend の Dockerfile
- `docker-compose.yml` / `docker-compose.dev.yml` / `docker-compose.ci.yml`
- Docker build context から `.env` や local storage を除外する `.dockerignore`
- GitHub Actions workflow: backend CI / frontend CI / Docker build / compose smoke

詳細な DB 制約・各 API の完成実装は次の大単位で進めます。

後続実装で CI に追加する予定の確認です。

- `/ready` の Qdrant / RAG model readiness 拡張
- worker 実ジョブ投入から完了までの smoke
- migration / seed の正式データモデル反映後の integration test
- RAG ask / citation / evaluation / audit log を含む final smoke
- backend dependency lockfile の正式化と Dockerfile の frozen install 化

## 破壊的操作

`docker compose down -v` は PostgreSQL、Qdrant、Ollama、アップロード済みファイルの volume を削除する可能性があります。対象は主に `ragproject_postgres_data`、`ragproject_qdrant_data`、`ragproject_ollama_data`、`ragproject_upload_storage` です。デモデータや検証データを消す操作なので、必要な場合だけ実行してください。

## PR-11 ingest indexing defaults

Document ingest now finishes extraction, chunking, deterministic fake embedding, Qdrant
collection ensure/upsert, and `document_versions.status=ready`. CI keeps
`EMBEDDING_PROVIDER=fake` and `EMBEDDING_FAKE_DIMENSION=8`, so `BAAI/bge-m3`, GPU, Ollama
model pull, and external API keys are not required for the default checks. For local
model experiments, set `EMBEDDING_PROVIDER=local`, keep `EMBEDDING_MODEL=BAAI/bge-m3`,
and set `EMBEDDING_VECTOR_DIMENSION=1024`.

## デモ

- [5分デモ手順](docs/demo/5-minute-demo.md)
- [サンプル質問集](docs/demo/sample-questions.md)
