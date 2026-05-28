#!/usr/bin/env sh
set -eu

MANIFEST="${MANIFEST:-app/experiments/manifests/phase2_retrieval_models.example.json}"
MODE="${MODE:-dry-run}"
DOWNLOAD_POLICY="${DOWNLOAD_POLICY:-}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"
OUTPUT_JSON="${OUTPUT_JSON:-../artifacts/experiments/retrieval_model_comparison.json}"
OUTPUT_MD="${OUTPUT_MD:-../artifacts/experiments/retrieval_model_comparison.md}"
SKIP_SEED_INDEXING="${SKIP_SEED_INDEXING:-false}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}/backend"
EXTRA_ARGS=""
if [ "${SKIP_SEED_INDEXING}" = "true" ]; then
  EXTRA_ARGS="--skip-seed-indexing"
fi
if [ -n "${DOWNLOAD_POLICY}" ]; then
  EXTRA_ARGS="${EXTRA_ARGS} --download-policy ${DOWNLOAD_POLICY}"
fi

uv run --extra experiments python -m app.experiments.run_retrieval_model_experiment \
  --manifest "${MANIFEST}" \
  --mode "${MODE}" \
  --timeout-seconds "${TIMEOUT_SECONDS}" \
  --output-json "${OUTPUT_JSON}" \
  --output-md "${OUTPUT_MD}" \
  ${EXTRA_ARGS}
