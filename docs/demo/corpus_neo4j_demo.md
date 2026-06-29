# Demo Corpus and Neo4j Demo Profile

This runbook keeps the demo reproducible without committing raw uploaded files,
extracted text dumps, chunks, Qdrant data, Neo4j data, logs, or `.env` values.

## Corpus

The primary demo corpus is the repository's own technical documentation listed
in [corpus_manifest.json](corpus_manifest.json). The manifest is the source of
truth for rebuilding the corpus. It intentionally points to files already in the
repository instead of storing copied text.

Included document groups:

- root `README.md`
- key `docs/phase3/*` GraphRAG and Neo4j docs
- key `docs/phase2/*` retrieval, evaluation, trace, and MCP docs
- database/API/design docs under `docs/`

`docs/cache_and_evaluation_overview.md` was considered from the handoff request,
but it is not present in this `origin/main@46dbbbc` checkout. The closest
current self docs are `docs/phase3/retrieval_cache_foundation.md` and
`docs/phase3/graph_evaluation_design.md`.

To add more technical documents, add entries to `corpus_manifest.json` using
only repo-owned, self-authored, explicitly permitted, or compatible
open-license content. Do not add third-party docs with unclear licensing, and
do not add generated raw chunks or downloaded corpora.

## Rebuild Corpus

Start the normal stack first:

```powershell
docker compose up -d --build
```

```sh
docker compose up -d --build
```

Then ingest the manifest through the existing document API. The Docker command
below uses the backend test image only as a reproducible Python runner; it talks
to the already-running backend over HTTP and does not change the ingest API. The
script logs only IDs, hashes, status/action codes, and file paths from the
manifest. It does not print raw document or chunk text.

```powershell
docker compose -f docker-compose.ci.yml run --build --rm --no-deps backend-test `
  python -m app.scripts.ingest_demo_corpus `
  --repo-root / `
  --base-url http://host.docker.internal:8000
```

```sh
docker compose -f docker-compose.ci.yml run --build --rm --no-deps backend-test \
  python -m app.scripts.ingest_demo_corpus \
  --repo-root / \
  --base-url http://host.docker.internal:8000
```

If you already have the backend Python environment on the host, the equivalent
host command is:

```powershell
cd backend
uv run python -m app.scripts.ingest_demo_corpus --repo-root ..
```

```sh
cd backend
uv run python -m app.scripts.ingest_demo_corpus --repo-root ..
```

Defaults use the local demo admin account `admin@example.com`. To avoid putting
credentials in shell history, set `RAG_DEMO_ADMIN_EMAIL` and
`RAG_DEMO_ADMIN_PASSWORD` in the shell environment when the local account differs.

The script is idempotent by manifest title and SHA-256 content hash:

- same title and active same hash: skip
- same title and ready same hash: approve if needed
- same title and new hash: upload as a new document version
- missing title: create a new logical document

By default it waits for ingest jobs to reach `ready` and approves ready versions
so the corpus is retrieval-eligible. Use `--no-wait` only when you want to queue
ingest and monitor the worker separately.

## Neo4j Demo Stack

PostgreSQL remains the source of truth. Neo4j is the default local and CI
read-model projection; if it is temporarily unavailable, the application starts
and graph retrieval records visible fallback reason codes.

One-command local demo:

```powershell
scripts\neo4j_demo.ps1
```

```sh
sh scripts/neo4j_demo.sh
```

The script:

1. uses `docker-compose.neo4j-demo.yml` as a small demo overlay on the default stack
2. builds backend/worker with the `neo4j` extra
3. ingests the manifest through the existing API
4. builds PostgreSQL graph indexes for active ready documents
5. runs Neo4j projection through `Neo4jProjectionService`
6. leaves the running backend configured with `GRAPH_STORE_PROVIDER=neo4j`
7. runs a retrieval evaluation smoke for `graph_postgres,graph_neo4j`

The local default Neo4j password is the non-secret Compose demo value
`change-me-local`. For any shared environment, set `NEO4J_PASSWORD` in your
shell before running the script and do not paste the value into docs, logs, or
PR comments.

To recreate backend and worker after changing local environment overrides:

```powershell
docker compose up -d --force-recreate --build backend worker
```

```sh
docker compose up -d --force-recreate --build backend worker
```

To stop the demo stack without deleting volumes:

```powershell
docker compose -f docker-compose.yml -f docker-compose.neo4j-demo.yml down
```

```sh
docker compose -f docker-compose.yml -f docker-compose.neo4j-demo.yml down
```

## Compare Providers

The existing evaluation runner already has graph comparison targets. After the
demo corpus and graph projection are built, compare both providers:

```powershell
scripts\run_retrieval_eval_smoke.ps1 `
  -Dataset phase3_graph_multi_hop `
  -Strategies graph_postgres,graph_neo4j `
  -ThresholdMode warn
```

```sh
DATASET=phase3_graph_multi_hop \
STRATEGIES=graph_postgres,graph_neo4j \
THRESHOLD_MODE=warn \
sh scripts/run_retrieval_eval_smoke.sh
```

If Neo4j is unavailable or the read model is not projected, graph retrieval
tries the PostgreSQL graph store when PostgreSQL graph sources exist and marks
the result with `neo4j_to_postgres_fallback`. This keeps the demo safe while
making the fallback visible in graph reason codes.

## Verification

Non-destructive checks:

```powershell
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.neo4j-demo.yml config --quiet
docker compose -f docker-compose.ci.yml run --build --rm backend-test sh -c "ruff format --check . && ruff check . && pytest tests/test_demo_corpus_scripts.py tests/test_graph_retrieval_strategy.py tests/test_retrieval_eval_smoke.py"
```

```sh
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.neo4j-demo.yml config --quiet
docker compose -f docker-compose.ci.yml run --build --rm backend-test sh -c 'ruff format --check . && ruff check . && pytest tests/test_demo_corpus_scripts.py tests/test_graph_retrieval_strategy.py tests/test_retrieval_eval_smoke.py'
```

Security and license checks:

- commit only manifest, scripts, docs, and code
- do not commit `storage/`, `reports/`, `artifacts/`, Qdrant volumes, Neo4j
  volumes, database dumps, or `.env`
- do not display raw chunks, raw documents, prompts, full context, PII, tokens,
  credentials, cookies, API keys, or Neo4j credentials
- add external documents only when ownership/license permission is clear
