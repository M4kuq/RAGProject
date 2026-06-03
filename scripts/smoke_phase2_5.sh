#!/usr/bin/env sh
set -eu

DEEP=0
K8S_DRY_RUN=0
RUN_EXISTING_PHASE2=0
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
SMOKE_ADMIN_EMAIL="${SMOKE_ADMIN_EMAIL:-}"
SMOKE_ADMIN_PASSWORD="${SMOKE_ADMIN_PASSWORD:-}"

for arg in "$@"; do
  case "$arg" in
    --deep) DEEP=1 ;;
    --k8s-dry-run) K8S_DRY_RUN=1 ;;
    --run-existing-phase2) RUN_EXISTING_PHASE2=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "[phase2.5-smoke] $*"; }
assert_path() {
  if [ ! -e "$1" ]; then
    echo "required Phase2.5 artifact is missing: $1" >&2
    exit 1
  fi
}
check_url() { curl -fsS "$1" >/dev/null; }

say "validate compose files without reading .env"
COMPOSE_DISABLE_ENV_FILE=1 docker compose config >/dev/null
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.ci.yml config --quiet

say "verify Phase2.5 docs and scripts"
for path in \
  docs/Phase2_Phase3_RAG拡張実装計画書_改訂版.md \
  docs/phase2/phase2_5_readme.md \
  docs/phase2/context_engineering_readme.md \
  docs/phase2/context_engineering_demo_scenario.md \
  docs/phase2/context_engineering_manual_test_cases.md \
  docs/phase2/context_engineering_acceptance_checklist.md \
  docs/phase2/context_engineering_known_limitations.md \
  docs/phase2/kubernetes_baseline.md \
  docs/phase2/kubernetes_local_baseline.md \
  docs/phase2/phase2_5_demo_scenario.md \
  docs/phase2/phase3_handoff.md \
  docs/phase2/deploy_aws_handoff.md \
  docs/phase2/llm_tool_calling_retrieval_orchestrator.md \
  docs/phase2/context_budget_trace_debug.md \
  docs/phase2/evidence_pack_context_compression.md \
  docs/phase2/tool_result_compression_orchestrator_guard.md \
  docs/phase2/mcp_advanced_rag_tools.md \
  k8s/local/kustomization.yaml \
  scripts/smoke_phase2_5.sh \
  scripts/k8s_smoke.sh \
  scripts/validate_k8s_manifests.py
do
  assert_path "$path"
done

say "run local manifest validator"
python scripts/validate_k8s_manifests.py

if command -v kubectl >/dev/null 2>&1; then
  say "render local k8s manifests"
  kubectl kustomize k8s/local >/dev/null
  if [ "$K8S_DRY_RUN" -eq 1 ]; then
    say "run client-side local k8s dry-run"
    kubectl apply --dry-run=client -k k8s/local >/dev/null
  fi
else
  say "kubectl not found; skipping k8s render and dry-run"
fi

say "check running backend/frontend if available"
if check_url "$BACKEND_URL/health"; then
  check_url "$BACKEND_URL/ready"
else
  say "backend is not reachable; start services before --deep"
fi
if ! check_url "$FRONTEND_URL"; then
  say "frontend is not reachable; UI smoke skipped"
fi

if [ "$RUN_EXISTING_PHASE2" -eq 1 ]; then
  say "run existing Phase2 smoke wrapper"
  sh scripts/smoke_phase2.sh
fi

if [ "$DEEP" -ne 1 ]; then
  say "basic Phase2.5 smoke completed"
  exit 0
fi

if ! check_url "$BACKEND_URL/ready"; then
  echo "Deep smoke requires a running ready backend at $BACKEND_URL" >&2
  exit 1
fi
if [ -z "$SMOKE_ADMIN_EMAIL" ] || [ -z "$SMOKE_ADMIN_PASSWORD" ]; then
  echo "Deep smoke requires SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD in the shell environment." >&2
  exit 1
fi

say "deep smoke checks require local app auth; credentials are not printed"
say "run Auto, Retrieval Debug, Context Budget, Evidence Pack, Tool Result Compression, and MCP checks manually from docs/phase2/phase2_5_demo_scenario.md"
say "deep Phase2.5 smoke completed"
