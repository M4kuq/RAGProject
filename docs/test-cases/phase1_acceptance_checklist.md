# Phase1 Acceptance Checklist

| Check | Status | Evidence | Notes |
|---|---|---|---|
| Auth | Pending | Backend CI auth tests / UI login demo | admin / viewer を確認する。 |
| CSRF | Pending | login、upload、approve、rag API の 403/成功確認 | pre-auth と session CSRF を分ける。 |
| RBAC | Pending | viewer direct admin access test | Forbidden または guard を確認する。 |
| Chat | Pending | Chat UI demo / backend chat tests | session history と send を確認する。 |
| Document upload | Pending | UI upload / smoke deep upload | 小さな Markdown を使う。 |
| Ingest | Pending | worker logs / document version ready | 環境差がある場合は known limitation に残す。 |
| Embedding / Qdrant | Pending | RAG search result / qdrant health | fake embedding で確認する。 |
| Retrieval | Pending | `/api/v1/rag/search` | selected result と score summary を見る。 |
| RAG ask | Pending | Chat UI / MCP rag_ask | answer と persisted messages を見る。 |
| Citation | Pending | citation panel / ask response | source label と snippet preview を見る。 |
| Confidence | Pending | confidence badge / retrieval run | label と score range を見る。 |
| UI | Pending | frontend lint/typecheck/build / manual UI demo | layout と error state を確認する。 |
| Admin UI | Pending | Admin Documents / Jobs / Evaluation | cache invalidation と redaction を見る。 |
| Evaluation | Pending | `phase1_smoke` run | queued/run detail/metrics を確認する。 |
| MCP | Pending | JSON-RPC stdio smoke | tools/list と rag_ask を確認する。 |
| README | Pending | required section review | setup、CI、MCP、troubleshooting を確認する。 |
| Demo | Pending | `docs/demo/5min_demo.md` | 5分デモを通す。 |
| CI | Pending | GitHub Actions checks | backend/frontend/docker/compose smoke を確認する。 |
| Docker | Pending | `docker compose config` | Windows/Ubuntu 手順と合わせる。 |
| Security | Pending | docs/security review | 機密値、raw context、full prompt を掲載しない。 |
| Cross OS | Pending | Windows + Ubuntu command review | 実機未確認は記録する。 |
| Known limitations | Pending | README / troubleshooting | Phase2 範囲を明示する。 |
