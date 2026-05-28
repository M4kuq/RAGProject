#!/usr/bin/env sh
set -eu

DATASET="${DATASET:-phase2_strategy_smoke}"
STRATEGIES="${STRATEGIES:-dense,hybrid,agentic_router}"
MODE="${MODE:-local}"
THRESHOLD_MODE="${THRESHOLD_MODE:-warn}"
CASE_LIMIT="${CASE_LIMIT:-5}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-300}"
OUTPUT_JSON="${OUTPUT_JSON:-../artifacts/retrieval_eval_smoke.json}"
OUTPUT_MD="${OUTPUT_MD:-../artifacts/retrieval_eval_smoke.md}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}/backend"
uv run --with "sentence-transformers>=2.7.0,<4" python -m app.scripts.retrieval_eval_smoke \
  --dataset "${DATASET}" \
  --strategies "${STRATEGIES}" \
  --mode "${MODE}" \
  --threshold-mode "${THRESHOLD_MODE}" \
  --case-limit "${CASE_LIMIT}" \
  --timeout-seconds "${TIMEOUT_SECONDS}" \
  --output-json "${OUTPUT_JSON}" \
  --output-md "${OUTPUT_MD}"
