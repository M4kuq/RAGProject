#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.ci.yml config
docker compose -f docker-compose.ci.yml build backend worker frontend backend-test frontend-test smoke
docker compose -f docker-compose.ci.yml run --rm backend-test
docker compose -f docker-compose.ci.yml run --rm frontend-test
if [ "${1:-}" = "--smoke" ]; then
  docker compose -f docker-compose.ci.yml up --abort-on-container-exit --exit-code-from smoke smoke
fi
