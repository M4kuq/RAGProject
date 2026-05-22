#!/usr/bin/env sh
set -eu

DEEP=0
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"

for arg in "$@"; do
  case "$arg" in
    --deep) DEEP=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "[phase1-smoke] $*"; }
check_url() { curl -fsS "$1" >/dev/null; }

say "validate compose files"
docker compose config >/dev/null
docker compose -f docker-compose.ci.yml config >/dev/null

say "check backend health when running"
if check_url "$BACKEND_URL/health"; then
  check_url "$BACKEND_URL/ready"
else
  say "backend is not reachable; run docker compose up --build or use --deep"
fi

say "check frontend when running"
if ! check_url "$FRONTEND_URL"; then
  say "frontend is not reachable; run docker compose up --build or use --deep"
fi

if [ "$DEEP" -ne 1 ]; then
  say "basic smoke completed"
  exit 0
fi

say "build and start Phase1 services"
docker compose build backend worker frontend migrate seed >/dev/null
docker compose run --rm migrate >/dev/null
docker compose run --rm seed >/dev/null
docker compose up -d backend worker frontend >/dev/null

say "wait for backend readiness"
attempt=1
while [ "$attempt" -le 30 ]; do
  if check_url "$BACKEND_URL/ready"; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
if [ "$attempt" -gt 30 ]; then
  echo "backend readiness failed" >&2
  exit 1
fi

say "check qdrant from backend network"
docker compose exec -T backend python -m app.scripts.healthcheck http://qdrant:6333/healthz >/dev/null

cookie_file="$(mktemp)"
upload_file="$(mktemp).md"
trap 'rm -f "$cookie_file" "$upload_file"' EXIT
cat >"$upload_file" <<'DOC'
# Phase1 smoke upload
This local smoke document confirms upload, ingest queue creation, and admin approval paths.
DOC

say "login with local demo admin"
csrf_json="$(curl -fsS -c "$cookie_file" "$BACKEND_URL/api/v1/auth/csrf")"
csrf_token="$(printf '%s' "$csrf_json" | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["csrf_token"])')"
login_json="$(curl -fsS -b "$cookie_file" -c "$cookie_file" -H "Content-Type: application/json" -H "X-CSRF-Token: $csrf_token" -d '{"email":"admin@example.com","password":"password"}' "$BACKEND_URL/api/v1/auth/login")"
csrf_token="$(printf '%s' "$login_json" | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["csrf_token"])')"

say "list seeded documents"
curl -fsS -b "$cookie_file" "$BACKEND_URL/api/v1/documents?page_size=5" >/dev/null

say "upload a small smoke document"
upload_json="$(curl -fsS -b "$cookie_file" -c "$cookie_file" -H "X-CSRF-Token: $csrf_token" -F "title=Phase1 Smoke Upload" -F "file=@$upload_file;type=text/markdown" "$BACKEND_URL/api/v1/documents")"
logical_document_id="$(printf '%s' "$upload_json" | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["logical_document_id"])')"
document_version_id="$(printf '%s' "$upload_json" | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["document_version_id"])')"

say "approve uploaded document version"
curl -fsS -b "$cookie_file" -H "X-CSRF-Token: $csrf_token" -X POST "$BACKEND_URL/api/v1/documents/$logical_document_id/versions/$document_version_id/approve" >/dev/null

say "run RAG search"
curl -fsS -b "$cookie_file" -H "Content-Type: application/json" -H "X-CSRF-Token: $csrf_token" -d '{"query":"What vector database is used by Phase1?","top_k":5,"rerank_top_n":2}' "$BACKEND_URL/api/v1/rag/search" >/dev/null

say "create evaluation run"
curl -fsS -b "$cookie_file" -H "Content-Type: application/json" -H "X-CSRF-Token: $csrf_token" -d '{"dataset_name":"phase1_smoke","case_limit":1}' "$BACKEND_URL/api/v1/evaluations/runs" >/dev/null

say "check MCP server version and tool list"
docker compose exec -T backend python -m app.mcp.server --version >/dev/null
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | docker compose exec -T backend python -m app.mcp.server >/dev/null

say "deep smoke completed"
