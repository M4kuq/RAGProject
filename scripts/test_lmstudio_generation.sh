#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M}"
BASE_URL="${LMSTUDIO_BASE_URL:-http://host.docker.internal:1234/v1}"

docker compose -f docker-compose.ci.yml build backend-test

docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e RUN_LMSTUDIO_GENERATION_TEST=true \
  -e LMSTUDIO_API_KEY=lm-studio \
  -e LMSTUDIO_BASE_URL="$BASE_URL" \
  -e LMSTUDIO_TIMEOUT_SECONDS=60 \
  -e GENERATION_MODEL_NAME="$MODEL" \
  ragproject-ci-backend-test \
  pytest tests/test_lmstudio_generation.py -k local_server -q
