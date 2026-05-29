# Phase2 手動テストケース

Phase2 の受け入れ確認で使う手動テストです。
実 secret、顧客データ、外部 API key、heavy model download は不要です。

| ID | Area | Scenario | Steps | Expected result | Notes |
|---|---|---|---|---|---|
| P2-TC-001 | 起動 / migration / seed | Compose 設定確認 | `docker compose config` と `docker compose -f docker-compose.ci.yml config --quiet` を実行する。 | 両方成功する。 | destructive cleanup はしない。 |
| P2-TC-002 | 起動 / migration / seed | サービス ready 確認 | サービスを起動し、`/health` と `/ready` を確認する。 | backend が healthy / ready。 | env 値は表示しない。 |
| P2-TC-003 | 起動 / migration / seed | seed data 確認 | demo admin でログインし、documents / evaluation datasets を開く。 | seeded documents と `phase2_strategy_smoke` が見える。 | local demo のみ。 |
| P2-TC-100 | Sparse Retrieval | sparse strategy が動く | keyword-heavy query で `/rag/search strategy=sparse` を実行する。 | `200 OK`。結果または safe empty list。trace は sparse。 | raw prompt / raw chunk を出さない。 |
| P2-TC-200 | Hybrid Retrieval | fusion score 確認 | `/rag/search strategy=hybrid` を実行する。 | dense / sparse / fused score summary が見える。 | deterministic ordering を確認。 |
| P2-TC-300 | Query Analyzer / Planner | query analysis 記録 | comparison / version query を検索する。 | Debug UI に intent、flags、safe query plan が表示される。 | full context なし。 |
| P2-TC-400 | Strategy Router | agentic_router 選択 | `/rag/search strategy=agentic_router` を実行する。 | selected/execution strategy と reason code が表示される。 | ask は opt-in。 |
| P2-TC-500 | Agentic Retrieval Loop | fallback なし確認 | 十分な根拠がある query で agentic search を実行する。 | `retrieval_call_count=1`、fallback false。 | Debug UI で確認。 |
| P2-TC-501 | Agentic Retrieval Loop | bounded fallback 確認 | 低スコアまたは diversity 不足になりやすい query を実行する。 | 設定上限内で fallback し、trace は safe。 | 無制限 loop なし。 |
| P2-TC-502 | Agentic Retrieval Loop | no-context ask | `/rag/ask strategy=agentic_router` で no-context query を送る。 | `422 no_context_found`。assistant message は作られない。 | user message 契約は維持。 |
| P2-TC-600 | Retrieval Debug UI v2 | trace 表示 | `/admin/retrieval-debug` と run detail を開く。 | plan / decision / settings / score / latency / items が safe に表示される。 | admin only。 |
| P2-TC-700 | Strategy Evaluation | multi-strategy run | `dense,hybrid,agentic_router` で evaluation run を作成する。 | case x strategy の item が作られ、完了または部分完了する。 | admin only / CSRF required。 |
| P2-TC-702 | Strategy Evaluation | failure promotion idempotency | failure candidates を 2 回 promote する。 | 1 回目は作成、2 回目は skipped / already exists。 | target dataset は active。 |
| P2-TC-800 | CI Retrieval Evaluation | workflow dispatch | GitHub Actions workflow の inputs を確認する。 | dataset、strategies、threshold mode 等が選べる。 | PR 必須 gate ではない。 |
| P2-TC-900 | LangSmith Optional Adapter | default no-op | LangSmith 設定なしで search/ask を実行する。 | RAG は成功し、export は skipped/no-op。 | secret 不要。 |
| P2-TC-1000 | SentenceTransformers Experiment | dry-run | `scripts/run_retrieval_model_experiment.ps1 -Mode dry-run -DownloadPolicy never` を実行する。 | JSON/Markdown artifact skeleton が出る。 | model download なし。 |
| P2-TC-1100 | Advanced Import | Office ingest | `.xlsx` / `.pptx` fixture を upload する。 | version ready。sheet / slide metadata が chunks に入る。 | legacy / macro file は拒否。 |
| P2-TC-1101 | Advanced Import | HTML/XML ingest | `.html` / `.htm` / `.xml` fixture を upload する。 | version ready。heading / XML path metadata が入る。 | SVG / DTD / entity は拒否。 |
| P2-TC-1102 | Advanced Import | URL ingest SSRF guard | safe mock URL と private redirect を試す。 | safe URL は version/job 作成、blocked URL は validation error。 | CI で外部 internet 不要。 |
| P2-TC-1200 | Document Diff | Version compare | 同一 logical document の 2 version を比較する。 | added / removed / changed / unchanged と bounded preview が表示される。 | admin only。 |
| P2-TC-1201 | Citation Navigation | Citation source preview | Chat citation から View source を開く。 | safe locator preview が開く。old-version / source URL 表示が正しい。 | viewer は preview のみ。 |
| P2-TC-1300 | Security / Redaction | 禁止データが出ない | API/UI/docs/artifact を確認する。 | raw prompt、full context、raw chunk text、PII、token、secret、storage path が出ない。 | synthetic data を使う。 |
| P2-TC-1500 | MCP Advanced RAG | strategy-aware search | MCP `rag_search strategy=hybrid` / `agentic_router` を呼ぶ。 | bounded snippet と safe trace summary が返る。 | raw chunk text なし。 |
| P2-TC-1600 | LLM Tool Orchestrator | Chat UI LLM Agentic RAG | Chat で **LLM Agentic RAG** を選び safe query を送る。 | `/rag/ask strategy=llm_tool_orchestrator` が送られ、citations/confidence 付き回答または `422 no_context_found` になる。 | viewer UI に internal trace は出さない。 |
| P2-TC-1601 | LLM Tool Orchestrator | bounded loop | local 設定で `LLM_ORCHESTRATOR_MAX_TOOL_CALLS` を低くして実行する。 | budget exhaustion は no-context になり、assistant placeholder は作られない。 | self-reflection loop なし。 |
| P2-TC-1602 | LLM Tool Orchestrator | safe trace | Retrieval Debug で `llm_tool_orchestrator` run を開く。 | tool count、tools used、finalize/budget flags、latency だけが表示される。 | raw prompt、full context、raw chunk text、token、secret なし。 |
