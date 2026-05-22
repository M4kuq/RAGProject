# Phase1 Manual Test Cases

各ケースは Phase1 最終受け入れ用である。実行環境、実行日、担当者、結果、証跡 URL または log 位置を validation report に記録する。

| ID | Area | Scenario | Steps | Expected result | Notes |
|---|---|---|---|---|---|
| TC-001 | 起動・health | compose config | `docker compose config` を実行する。 | config が成功する。 | Windows / Ubuntu 両方で確認する。 |
| TC-002 | 起動・health | CI compose config | `docker compose -f docker-compose.ci.yml config` を実行する。 | config が成功する。 | fake adapter env を確認する。 |
| TC-003 | 起動・health | service startup | `docker compose up --build` を実行する。 | backend、frontend、postgres、qdrant、worker が起動する。 | long-running logs を保存する。 |
| TC-004 | 起動・health | backend health | `/health` を確認する。 | 200 が返る。 | liveness のみ。 |
| TC-005 | 起動・health | backend readiness | `/ready` を確認する。 | DB readiness を含む 200 が返る。 | Qdrant readiness は別確認。 |
| TC-006 | 起動・health | frontend health | `http://localhost:5173` を開く。 | UI が表示される。 | browser console も見る。 |
| TC-007 | 起動・health | postgres readiness | compose health を確認する。 | postgres が healthy になる。 | `docker compose ps`。 |
| TC-008 | 起動・health | qdrant readiness | backend container から qdrant health を確認する。 | health check が成功する。 | host port は公開しない構成でもよい。 |
| TC-009 | 起動・health | basic smoke | `scripts/smoke_phase1.*` を実行する。 | basic smoke が成功する。 | deep は別ケース。 |
| TC-101 | 認証・認可 | pre-auth CSRF | `/api/v1/auth/csrf` を呼ぶ。 | csrf token が返る。 | 値は記録しない。 |
| TC-102 | 認証・認可 | admin login | admin demo account で login する。 | user と session が作られる。 | dummy credential。 |
| TC-103 | 認証・認可 | viewer login | viewer demo account で login する。 | viewer role で login できる。 | dummy credential。 |
| TC-104 | 認証・認可 | invalid login | 誤った credential で login する。 | 認証失敗が返る。 | detail に機密情報が出ない。 |
| TC-105 | 認証・認可 | me endpoint | login 後 `/auth/me` を呼ぶ。 | role と user summary が返る。 | session token は出ない。 |
| TC-106 | 認証・認可 | logout | logout を実行する。 | session が無効化される。 | 再度 `/me` は失敗する。 |
| TC-107 | 認証・認可 | admin RBAC | admin で admin API を呼ぶ。 | 成功する。 | documents/evaluations で確認。 |
| TC-108 | 認証・認可 | viewer RBAC | viewer で admin API を呼ぶ。 | Forbidden または guard になる。 | UI direct route も確認。 |
| TC-109 | 認証・認可 | CSRF missing | state-changing API を CSRF なしで呼ぶ。 | 403 系が返る。 | upload/approve/logout で確認。 |
| TC-201 | 文書管理 | seed documents | admin documents 一覧を見る。 | seed documents が見える。 | 4文書以上。 |
| TC-202 | 文書管理 | old/new pair | Phase1 Design Memo の versions を見る。 | old version と active version がある。 | v2 が active。 |
| TC-203 | 文書管理 | upload markdown | 小さな Markdown を upload する。 | logical document と version が作成される。 | raw content は log に出さない。 |
| TC-204 | 文書管理 | upload txt | 小さな TXT を upload する。 | upload が成功する。 | allowlist 確認。 |
| TC-205 | 文書管理 | upload csv | 小さな CSV を upload する。 | upload が成功する。 | demo CSV と混同しない。 |
| TC-206 | 文書管理 | reject extension | allowlist 外の拡張子を upload する。 | 4xx が返る。 | file 内容は公開可能なもの。 |
| TC-207 | 文書管理 | size limit | size limit 超過を試す。 | 413 または validation error。 | 実行しにくい場合は設計確認。 |
| TC-208 | 文書管理 | list documents pagination | page / page_size を変える。 | pagination meta が正しい。 | UI も確認。 |
| TC-209 | 文書管理 | search documents | q/title filter を使う。 | 対象文書だけが返る。 | admin only。 |
| TC-210 | 文書管理 | version detail | version detail を開く。 | metadata と status が見える。 | storage key は出さない。 |
| TC-211 | 文書管理 | chunks list | chunks を見る。 | preview と metadata が返る。 | raw full chunk を UI に出さない。 |
| TC-212 | 文書管理 | approve version | pending/ready version を approve する。 | active version が更新される。 | cache invalidation 確認。 |
| TC-213 | 文書管理 | duplicate upload | 同じ内容を再 upload する。 | idempotent または duplicate response になる。 | 仕様通り。 |
| TC-214 | 文書管理 | add version | 既存 logical document に version を追加する。 | version_no が増える。 | old version が残る。 |
| TC-215 | 文書管理 | archive document | document を archive する。 | 一覧 status が archived になる。 | 必要な demo 文書では実施注意。 |
| TC-216 | 文書管理 | archived filter | archived filter を使う。 | archived 文書が絞り込まれる。 | admin UI でも確認。 |
| TC-217 | 文書管理 | viewer denied | viewer で document admin API を呼ぶ。 | Forbidden になる。 | RBAC 再確認。 |
| TC-218 | 文書管理 | upload logs | upload 時の logs を見る。 | credential、raw file full text が出ない。 | redaction。 |
| TC-219 | 文書管理 | storage persistence | restart 後に uploaded file metadata を見る。 | metadata が残る。 | volume 前提。 |
| TC-301 | Worker / Job | worker startup | worker service を起動する。 | health または DB check が成功する。 | compose smoke。 |
| TC-302 | Worker / Job | job list | Admin Jobs を開く。 | jobs が一覧表示される。 | raw payload は出さない。 |
| TC-303 | Worker / Job | upload job | upload 後 job を確認する。 | ingest 系 job が見える。 | enabled type に依存。 |
| TC-304 | Worker / Job | lease | running job の lease field を確認する。 | locked_by/lease が安全に見える。 | raw payload は省略。 |
| TC-305 | Worker / Job | retry failed | failed job がある場合 retry する。 | retry job が作られる。 | 無い場合は設計確認。 |
| TC-306 | Worker / Job | retry guard | active retry 中に再 retry する。 | duplicate retry が抑止される。 | API/DB 制約。 |
| TC-307 | Worker / Job | worker logs | worker logs を見る。 | token、credential、raw content が出ない。 | redaction。 |
| TC-308 | Worker / Job | shutdown | compose stop 後再起動する。 | jobs が矛盾しない。 | local volume 前提。 |
| TC-309 | Worker / Job | disabled profile | CI profile の worker 設定を見る。 | 重い job を必須にしない。 | fake adapter。 |
| TC-401 | RAG search | admin search | admin で `/rag/search` を呼ぶ。 | retrieval_run と items が返る。 | CSRF 必須。 |
| TC-402 | RAG search | viewer denied | viewer で `/rag/search` を呼ぶ。 | Forbidden になる。 | admin debug API。 |
| TC-403 | RAG search | top_k bounds | top_k 1/20/21 を試す。 | 範囲外は validation error。 | schema。 |
| TC-404 | RAG search | rerank bounds | rerank_top_n 1/20/21 を試す。 | 範囲外は validation error。 | schema。 |
| TC-405 | RAG search | seed question | Qdrant 質問を検索する。 | Qdrant 文書が上位に出る。 | sample question。 |
| TC-406 | RAG search | filters | logical_document_ids filter を使う。 | 対象文書に限定される。 | positive id のみ。 |
| TC-407 | RAG search | score summary | score summary を確認する。 | selected_count などが整合する。 | retrieval trace。 |
| TC-408 | RAG search | snippet safety | snippet を確認する。 | preview 長に収まる。 | full context ではない。 |
| TC-409 | RAG search | no candidates | 関係ない質問を検索する。 | empty または low relevance として扱う。 | error にならない。 |
| TC-501 | RAG ask / citation / confidence | chat session create | Chat UI で session を作る。 | session が作成される。 | route session も確認。 |
| TC-502 | RAG ask / citation / confidence | ask seed question | `What vector database is used by Phase1?` を送る。 | 回答が返る。 | fake generation 可。 |
| TC-503 | RAG ask / citation / confidence | idempotency | 同じ client_message_id で再送する。 | replay 扱いになる。 | duplicate message を作らない。 |
| TC-504 | RAG ask / citation / confidence | citation panel | citation panel を開く。 | citation が表示される。 | source label sanitized。 |
| TC-505 | RAG ask / citation / confidence | citation old version | old/new pair 由来の citation を確認する。 | old_version_flag が適切。 | active v2 優先。 |
| TC-506 | RAG ask / citation / confidence | confidence badge | confidence を見る。 | score と label が出る。 | 0..1 range。 |
| TC-507 | RAG ask / citation / confidence | groundedness | response / DB trace を見る。 | groundedness score が保存される。 | failed run は null。 |
| TC-508 | RAG ask / citation / confidence | no context | 文書外質問を送る。 | no_context 系の error/表示になる。 | 断定しない。 |
| TC-509 | RAG ask / citation / confidence | validation blank | 空 message を送る。 | validation error。 | UI でも抑止。 |
| TC-510 | RAG ask / citation / confidence | validation long | max 超過 message を送る。 | validation error。 | 実行しにくい場合は schema 確認。 |
| TC-511 | RAG ask / citation / confidence | session owner | 他人 session に append する。 | Forbidden / not found。 | data boundary。 |
| TC-512 | RAG ask / citation / confidence | archived session | archived session に送る。 | append できない。 | chat service。 |
| TC-513 | RAG ask / citation / confidence | temporary session | temporary chat を確認する。 | TTL が設定される。 | UI 表示。 |
| TC-514 | RAG ask / citation / confidence | answer text safety | assistant text を確認する。 | raw prompt/full context が出ない。 | redaction。 |
| TC-515 | RAG ask / citation / confidence | replay citation scope | replay 時 citation が同じ run に閉じる。 | stale citation が混ざらない。 | regression。 |
| TC-516 | RAG ask / citation / confidence | marker parser | marker だけの回答を確認する。 | citation_build_failed 等で扱う。 | regression。 |
| TC-517 | RAG ask / citation / confidence | unknown marker | 未知 marker を含む回答を確認する。 | safe failure になる。 | regression。 |
| TC-518 | RAG ask / citation / confidence | UI route guard | existing-session URL から送信する。 | session detail 待ちで送る。 | PR-15 regression。 |
| TC-519 | RAG ask / citation / confidence | audit trace | retrieval run を確認する。 | query_hash と request_id が保存される。 | query raw は保存しない。 |
| TC-601 | Evaluation | create run | `phase1_smoke` run を作る。 | queued run と job が作られる。 | admin only。 |
| TC-602 | Evaluation | list runs | runs 一覧を見る。 | dataset、status、counts が出る。 | pagination。 |
| TC-603 | Evaluation | run detail | detail を開く。 | items と metrics が出る。 | full prompt は出ない。 |
| TC-604 | Evaluation | invalid dataset | 存在しない dataset を指定する。 | validation / not found。 | safe error。 |
| TC-605 | Evaluation | case limit | case_limit を 1 にする。 | 1件で作成される。 | 1..50。 |
| TC-606 | Evaluation | fixture safety | fixture を見る。 | PII/credential がない。 | docs review。 |
| TC-607 | Evaluation | worker processing | worker enabled 時に処理する。 | succeeded/failed に遷移する。 | 環境差あり。 |
| TC-608 | Evaluation | UI redaction | UI detail を見る。 | raw context/full prompt が出ない。 | security。 |
| TC-609 | Evaluation | MCP listing | MCP `list_evaluation_runs` を使う。 | safe summary が返る。 | local-only。 |
| TC-701 | MCP | startup version | `python -m app.mcp.server --version` を実行する。 | version が出る。 | backend cwd。 |
| TC-702 | MCP | initialize | JSON-RPC initialize を送る。 | capabilities が返る。 | stdio。 |
| TC-703 | MCP | tools/list | `tools/list` を送る。 | 7 tools が返る。 | write tools なし。 |
| TC-704 | MCP | rag_search | `rag_search` を呼ぶ。 | snippets のみ返る。 | raw context なし。 |
| TC-705 | MCP | rag_ask | `rag_ask` を呼ぶ。 | answer、citations、confidence が返る。 | local-only。 |
| TC-706 | MCP | list_documents | `list_documents` を呼ぶ。 | metadata summary が返る。 | archived は指定時のみ。 |
| TC-707 | MCP | get_document_status | existing ID を指定する。 | version summary と chunk count が返る。 | full text なし。 |
| TC-708 | MCP | get_document_status not found | missing ID を指定する。 | not found error。 | safe error。 |
| TC-709 | MCP | get_job_status | existing job ID を指定する。 | redacted payload/result summary。 | raw payload なし。 |
| TC-710 | MCP | get_job_status not found | missing job ID を指定する。 | not found error。 | safe error。 |
| TC-711 | MCP | list_evaluation_runs | list を呼ぶ。 | summary が返る。 | no rerun。 |
| TC-712 | MCP | get_evaluation_result | existing run ID を指定する。 | metrics と case summary。 | prompt/context なし。 |
| TC-713 | MCP | resources/list | resources/list を呼ぶ。 | resources が返る。 | PR-18。 |
| TC-714 | MCP | resources/read | safe resource を読む。 | safe content が返る。 | raw secrets なし。 |
| TC-715 | MCP | prompts/list | prompts/list を呼ぶ。 | prompts が返る。 | full context なし。 |
| TC-716 | MCP | invalid id | unsafe request id を送る。 | invalid request。 | token-like id を拒否。 |
| TC-717 | MCP | invalid params | extra property を送る。 | invalid params。 | schema。 |
| TC-718 | MCP | disabled | MCP_ENABLED=false で起動する。 | disabled error。 | env profile。 |
| TC-719 | MCP | client config | local client 設定例で接続する。 | tools が見える。 | 実機未確認なら記録。 |
| TC-801 | Security / redaction | README review | README を確認する。 | `.env` 値、credential 実値、raw context がない。 | dummy credential 以外。 |
| TC-802 | Security / redaction | docs review | docs を検索する。 | token/session/csrf 値がない。 | placeholders のみ。 |
| TC-803 | Security / redaction | fixture review | evaluation fixture を見る。 | PII/credential がない。 | public data。 |
| TC-804 | Security / redaction | logs review | backend/worker logs を見る。 | password_hash、cookie、raw chunks がない。 | sampled logs。 |
| TC-805 | Security / redaction | UI redaction | admin job/eval UI を見る。 | raw payload/full context が出ない。 | PR-16/17。 |
| TC-806 | Security / redaction | MCP redaction | MCP job/eval tools を見る。 | redacted summary のみ。 | PR-18。 |
| TC-807 | Security / redaction | cleanup warning | README の volume 初期化注意を見る。 | `docker compose down -v` の影響が明記される。 | 自動実行しない。 |
| TC-808 | Security / redaction | RBAC direct route | viewer で admin route 直打ち。 | access denied。 | UI/API。 |
| TC-809 | Security / redaction | external exposure | compose ports を見る。 | local bind または local-only 前提。 | MCP remote なし。 |
| TC-901 | Cross OS / CI / final smoke | Windows basic | Windows で `smoke_phase1.ps1` を実行する。 | 成功する。 | 実機未実行なら記録。 |
| TC-902 | Cross OS / CI / final smoke | Windows deep | Windows で `smoke_phase1.ps1 -Deep` を実行する。 | optional deep が成功する。 | 時間がかかる。 |
| TC-903 | Cross OS / CI / final smoke | Ubuntu basic | Ubuntu で `sh scripts/smoke_phase1.sh` を実行する。 | 成功する。 | 実機未実行なら記録。 |
| TC-904 | Cross OS / CI / final smoke | Ubuntu deep | Ubuntu で `sh scripts/smoke_phase1.sh --deep` を実行する。 | optional deep が成功する。 | 時間がかかる。 |
| TC-905 | Cross OS / CI / final smoke | backend CI | Backend CI を確認する。 | ruff/mypy/pytest が成功する。 | GitHub Actions。 |
| TC-906 | Cross OS / CI / final smoke | frontend CI | Frontend CI を確認する。 | lint/typecheck/test/build が成功する。 | GitHub Actions。 |
| TC-907 | Cross OS / CI / final smoke | docker CI | Docker CI を確認する。 | build と compose config が成功する。 | GitHub Actions。 |
| TC-908 | Cross OS / CI / final smoke | compose smoke CI | Compose Smoke を確認する。 | migration/seed/readiness が成功する。 | fake adapter。 |
| TC-909 | Cross OS / CI / final smoke | final 5min demo | `docs/demo/5min_demo.md` を通す。 | 5分で主要導線を説明できる。 | 証跡を残す。 |
