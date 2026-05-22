# Phase1 Validation Report Template

| Area | Command / Check | Result | Evidence | Notes |
|---|---|---|---|---|
| Backend | `cd backend && uv run --extra dev ruff format --check .` | Pending |  |  |
| Backend | `cd backend && uv run --extra dev ruff check .` | Pending |  |  |
| Backend | `cd backend && uv run --extra dev mypy .` | Pending |  |  |
| Backend | `cd backend && uv run --extra dev pytest` | Pending |  |  |
| Frontend | `cd frontend && npm run lint` | Pending |  |  |
| Frontend | `cd frontend && npm run typecheck` | Pending |  |  |
| Frontend | `cd frontend && npm test` | Pending |  |  |
| Frontend | `cd frontend && npm run build` | Pending |  |  |
| Docker | `docker compose config` | Pending |  |  |
| Docker CI | `docker compose -f docker-compose.ci.yml config` | Pending |  |  |
| Smoke | `scripts/smoke_phase1.*` | Pending |  |  |
| Demo | `docs/demo/5min_demo.md` | Pending |  |  |
| MCP | `tools/list` and `rag_ask` | Pending |  |  |
| Security | docs / fixtures redaction review | Pending |  |  |
| PR | GitHub Actions | Pending |  |  |
