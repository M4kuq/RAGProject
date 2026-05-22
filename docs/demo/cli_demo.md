# CLI Demo

## Health

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
```

## Migration / Seed

```bash
docker compose run --rm migrate
docker compose run --rm seed
```

seed は繰り返し実行しても同じ demo account と demo documents を再利用する。

## Smoke

```bash
sh scripts/smoke_phase1.sh
sh scripts/smoke_phase1.sh --deep
```

Windows では次を使う。

```powershell
.\scripts\smoke_phase1.ps1
.\scripts\smoke_phase1.ps1 -Deep
```

basic smoke は compose config と起動済み endpoint を確認する。deep smoke は migration、seed、login、upload、approve、RAG search、evaluation 作成、MCP startup を確認する。

## API Login Example

```bash
cookie_file=$(mktemp)
csrf_json=$(curl -fsS -c "$cookie_file" http://localhost:8000/api/v1/auth/csrf)
csrf_token=$(printf '%s' "$csrf_json" | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["csrf_token"])')
curl -fsS -b "$cookie_file" -c "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  -d '{"email":"admin@example.com","password":"password"}' \
  http://localhost:8000/api/v1/auth/login >/dev/null
```

この credential はローカルデモ用である。

## RAG Search Example

```bash
curl -fsS -b "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  -d '{"query":"What vector database is used by Phase1?","top_k":5,"rerank_top_n":2}' \
  http://localhost:8000/api/v1/rag/search
```

## Evaluation Example

```bash
curl -fsS -b "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  -d '{"dataset_name":"phase1_smoke","case_limit":1}' \
  http://localhost:8000/api/v1/evaluations/runs
```
