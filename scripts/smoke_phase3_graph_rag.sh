#!/usr/bin/env sh
set -eu

DEEP=0
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)

for arg in "$@"; do
  case "$arg" in
    --deep) DEEP=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "[phase3-graph-smoke] $*"; }
assert_path() {
  if [ ! -e "$1" ]; then
    echo "required GraphRAG artifact is missing: $1" >&2
    exit 1
  fi
}
assert_contains() {
  if ! grep -F "$2" "$1" >/dev/null; then
    echo "required GraphRAG text is missing from $1: $2" >&2
    exit 1
  fi
}
check_url() { curl -fsS "$1" >/dev/null 2>&1; }

cd "$REPO_ROOT"

say "validate compose files without reading .env"
COMPOSE_DISABLE_ENV_FILE=1 docker compose config --quiet
COMPOSE_DISABLE_ENV_FILE=1 docker compose --profile neo4j config --quiet

say "verify GraphRAG final docs and helper artifacts"
for path in \
  README.md \
  docs/phase3/README.md \
  docs/phase3/graph_rag_final_readme.md \
  docs/phase3/graph_rag_demo_scenario.md \
  docs/phase3/graph_rag_manual_test_cases.md \
  docs/phase3/graph_rag_acceptance_checklist.md \
  docs/phase3/graph_rag_known_limitations.md \
  docs/phase3/graph_rag_next_phase_handoff.md \
  docs/phase3/graph_rag_architecture.md \
  docs/phase3/graph_retrieval_strategy.md \
  docs/phase3/neo4j_optional_backend.md \
  docs/phase3/retrieval_cache_foundation.md \
  docs/phase3/graph_evaluation_design.md \
  docs/phase3/security_redaction_policy.md \
  backend/app/evaluation/fixtures/phase3_graph_multi_hop.json \
  backend/app/scripts/queue_graph_index_builds.py \
  scripts/smoke_phase3_graph_rag.ps1 \
  scripts/smoke_phase3_graph_rag.sh
do
  assert_path "$path"
done

say "verify key GraphRAG handoff statements"
assert_contains docs/phase3/graph_rag_final_readme.md "PostgreSQL is the source of truth"
assert_contains docs/phase3/graph_rag_final_readme.md "Neo4j, when enabled, is only a read model"
assert_contains docs/phase3/graph_rag_final_readme.md "RETRIEVAL_CACHE_ENABLED=false"
assert_contains docs/phase3/graph_rag_final_readme.md "phase3_graph_multi_hop"
assert_contains docs/phase3/graph_rag_acceptance_checklist.md "Raw text and secret non-storage"
assert_contains docs/phase3/graph_rag_known_limitations.md "graph_hybrid"
assert_contains docker-compose.yml "GRAPH_RETRIEVAL_ENABLED"
assert_contains .env.example "GRAPH_RETRIEVAL_ENABLED=false"

say "check running backend/frontend if available"
if check_url "$BACKEND_URL/health"; then
  check_url "$BACKEND_URL/ready"
else
  say "backend is not reachable; start services for runtime demo checks"
fi
if ! check_url "$FRONTEND_URL"; then
  say "frontend is not reachable; UI demo checks skipped"
fi

if [ "$DEEP" -eq 1 ]; then
  say "deep mode validates helper import without queueing jobs"
  docker compose exec -T backend python -m app.scripts.queue_graph_index_builds --dry-run >/dev/null
fi

say "GraphRAG smoke completed"
