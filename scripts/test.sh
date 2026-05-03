#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.ci.yml config
docker compose -f docker-compose.ci.yml build backend worker frontend-build backend-test frontend-test smoke
docker compose -f docker-compose.ci.yml run --rm backend-test
docker compose -f docker-compose.ci.yml run --rm frontend-test
if [ "${1:-}" = "--smoke" ]; then
  docker compose -f docker-compose.ci.yml run --rm frontend-build
  docker compose -f docker-compose.ci.yml up -d backend worker
  attempt=1
  while [ "$attempt" -le 24 ]; do
    worker_id="$(docker compose -f docker-compose.ci.yml ps -q worker)"
    if [ -n "$worker_id" ] \
      && [ "$(docker inspect -f '{{.State.Running}}' "$worker_id")" = "true" ] \
      && [ "$(docker inspect -f '{{.State.Health.Status}}' "$worker_id")" = "healthy" ]; then
      break
    fi
    echo "worker not healthy yet; retry ${attempt}/24"
    attempt=$((attempt + 1))
    sleep 5
  done
  if [ "$attempt" -gt 24 ]; then
    exit 1
  fi
  attempt=1
  while [ "$attempt" -le 24 ]; do
    if docker compose -f docker-compose.ci.yml run --rm --no-deps smoke; then
      exit 0
    fi
    echo "compose smoke not ready yet; retry ${attempt}/24"
    attempt=$((attempt + 1))
    sleep 5
  done
  exit 1
fi
