#!/usr/bin/env sh
set -eu

DEEP=0
RUN_RETRIEVAL_EVAL=0
RUN_EXPERIMENT_DRY_RUN=0
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
SMOKE_ADMIN_EMAIL="${SMOKE_ADMIN_EMAIL:-}"
SMOKE_ADMIN_PASSWORD="${SMOKE_ADMIN_PASSWORD:-}"

for arg in "$@"; do
  case "$arg" in
    --deep) DEEP=1 ;;
    --run-retrieval-eval) RUN_RETRIEVAL_EVAL=1 ;;
    --run-experiment-dry-run) RUN_EXPERIMENT_DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "[phase2-smoke] $*"; }
check_url() { curl -fsS "$1" >/dev/null; }
assert_path() {
  if [ ! -e "$1" ]; then
    echo "required Phase2 artifact is missing: $1" >&2
    exit 1
  fi
}
json_data_field() {
  python -c '
import json
import sys

payload = json.load(sys.stdin)
print(payload["data"][sys.argv[1]])
' "$1"
}

say "validate compose files"
COMPOSE_DISABLE_ENV_FILE=1 docker compose config >/dev/null
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.ci.yml config --quiet

say "verify Phase2 final docs and artifacts"
for path in \
  docs/phase2/README.md \
  docs/phase2/phase2_demo_scenario.md \
  docs/phase2/phase2_manual_test_cases.md \
  docs/phase2/phase2_acceptance_checklist.md \
  docs/phase2/phase2_known_limitations.md \
  docs/phase2/phase3_handoff.md \
  docs/phase2/demo_fixtures/phase2_source_feed.xml \
  docs/phase2/demo_fixtures/phase2_source_page.html \
  docs/phase2/demo_fixtures/phase2_strategy_overview.xlsx \
  docs/phase2/demo_fixtures/phase2_strategy_walkthrough.pptx \
  docs/phase2/retrieval_debug_ui_v2.md \
  docs/phase2/agentic_retrieval_loop.md \
  docs/phase2/agentic_strategy_evaluation.md \
  docs/phase2/ci_retrieval_evaluation.md \
  docs/phase2/langsmith_optional_adapter.md \
  docs/phase2/sentence_transformers_experiment_harness.md \
  docs/phase2/advanced_import_office.md \
  docs/phase2/advanced_import_html_xml_url.md \
  docs/phase2/document_diff_version_compare.md \
  docs/phase2/citation_navigation.md \
  backend/app/evaluation/fixtures/phase2_strategy_smoke.json \
  backend/app/experiments/manifests/phase2_retrieval_models.example.json \
  .github/workflows/retrieval-eval-smoke.yml
do
  assert_path "$path"
done

say "check backend health when running"
if check_url "$BACKEND_URL/health"; then
  check_url "$BACKEND_URL/ready"
else
  say "backend is not reachable; start services or rerun with --deep after startup"
fi

say "check frontend when running"
if ! check_url "$FRONTEND_URL"; then
  say "frontend is not reachable; start services if UI smoke is needed"
fi

if [ "$RUN_EXPERIMENT_DRY_RUN" -eq 1 ]; then
  say "run SentenceTransformers experiment dry-run without model download"
  MODE=dry-run DOWNLOAD_POLICY=never SKIP_SEED_INDEXING=true \
    sh scripts/run_retrieval_model_experiment.sh
fi

if [ "$RUN_RETRIEVAL_EVAL" -eq 1 ]; then
  say "run retrieval evaluation smoke wrapper"
  DATASET=phase2_strategy_smoke \
    STRATEGIES=dense,hybrid,agentic_router \
    THRESHOLD_MODE=warn \
    CASE_LIMIT=5 \
    scripts/run_retrieval_eval_smoke.sh
fi

if [ "$DEEP" -ne 1 ]; then
  say "basic Phase2 smoke completed"
  exit 0
fi

if ! check_url "$BACKEND_URL/ready"; then
  echo "Deep smoke requires a running ready backend at $BACKEND_URL" >&2
  exit 1
fi
if [ -z "$SMOKE_ADMIN_EMAIL" ] || [ -z "$SMOKE_ADMIN_PASSWORD" ]; then
  echo "Deep smoke requires SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD to be set for the local demo admin." >&2
  exit 1
fi

cookie_file="$(mktemp)"
login_body_file="$(mktemp)"
trap 'rm -f "$cookie_file" "$login_body_file"' EXIT

say "sign in with local demo admin"
csrf_json="$(curl -fsS -c "$cookie_file" "$BACKEND_URL/api/v1/auth/csrf")"
csrf_token="$(printf '%s' "$csrf_json" | json_data_field csrf_token)"
python -c '
import json
import os
import sys

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(
        {
            "email": os.environ["SMOKE_ADMIN_EMAIL"],
            "password": os.environ["SMOKE_ADMIN_PASSWORD"],
        },
        handle,
        separators=(",", ":"),
    )
' "$login_body_file"
login_json="$(curl -fsS -b "$cookie_file" -c "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  -d "@$login_body_file" \
  "$BACKEND_URL/api/v1/auth/login")"
csrf_token="$(printf '%s' "$login_json" | json_data_field csrf_token)"

say "run dense/sparse/hybrid/agentic_router safe searches"
for strategy in dense sparse hybrid agentic_router; do
  curl -fsS -b "$cookie_file" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf_token" \
    -d "{\"query\":\"Phase2 retrieval strategy overview\",\"top_k\":5,\"rerank_top_n\":2,\"strategy\":\"$strategy\"}" \
    "$BACKEND_URL/api/v1/rag/search" >/dev/null
done

say "verify retrieval debug endpoint is reachable for admin"
search_json="$(curl -fsS -b "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  -d '{"query":"Phase2 debug trace fields","top_k":3,"rerank_top_n":1,"strategy":"agentic_router"}' \
  "$BACKEND_URL/api/v1/rag/search")"
run_id="$(printf '%s' "$search_json" | json_data_field retrieval_run_id)"
if [ -n "$run_id" ]; then
  curl -fsS -b "$cookie_file" "$BACKEND_URL/api/v1/rag/retrieval-runs/$run_id" >/dev/null
fi

say "deep Phase2 smoke completed"
