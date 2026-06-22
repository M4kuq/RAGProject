#!/usr/bin/env sh
set -eu

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}"
MANIFEST="${MANIFEST:-docs/demo/corpus_manifest.json}"
DATASET="${DATASET:-phase3_graph_multi_hop}"
STRATEGIES="${STRATEGIES:-graph_postgres,graph_neo4j}"
CASE_LIMIT="${CASE_LIMIT:-5}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-300}"
SKIP_CORPUS="${SKIP_CORPUS:-0}"
SKIP_EVALUATION="${SKIP_EVALUATION:-0}"
NO_BUILD="${NO_BUILD:-0}"

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)

say() { printf '%s\n' "[neo4j-demo] $*"; }
compose() {
  docker compose -f docker-compose.yml -f docker-compose.neo4j-demo.yml --profile neo4j "$@"
}
require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required" >&2
    exit 1
  fi
}
wait_http_ready() {
  url="$1"
  timeout="$2"
  start=$(date +%s)
  while :; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "$timeout" ]; then
      echo "timed out waiting for $url" >&2
      exit 1
    fi
    sleep 2
  done
}
require docker
require curl

export NEO4J_USER="${NEO4J_USER:-neo4j}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-change-me-local}"

cd "$REPO_ROOT"

say "validate compose config"
docker compose config --quiet
compose config --quiet

say "start compose stack with neo4j profile"
if [ "$NO_BUILD" = "1" ]; then
  compose up -d
else
  compose up -d --build
fi

say "wait for backend readiness"
wait_http_ready "$BACKEND_URL/ready" "$TIMEOUT_SECONDS"

if [ "$SKIP_CORPUS" != "1" ]; then
  say "ingest reproducible demo corpus through the existing API"
  compose exec -T backend python -m app.scripts.ingest_demo_corpus \
    --repo-root /workspace \
    --manifest "$MANIFEST" \
    --base-url http://127.0.0.1:8000
fi

say "build PostgreSQL graph index and run optional Neo4j projection"
compose exec -T backend python -m app.scripts.build_demo_graph_index

if [ "$SKIP_EVALUATION" != "1" ]; then
  say "compare graph_postgres and graph_neo4j with existing evaluation runner"
  compose exec -T backend python -m app.scripts.retrieval_eval_smoke \
    --dataset "$DATASET" \
    --strategies "$STRATEGIES" \
    --threshold-mode warn \
    --case-limit "$CASE_LIMIT" \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    --output-json /tmp/ragproject_graph_provider_eval.json \
    --output-md /tmp/ragproject_graph_provider_eval.md
fi

say "Neo4j demo profile is running with GRAPH_STORE_PROVIDER=neo4j"
