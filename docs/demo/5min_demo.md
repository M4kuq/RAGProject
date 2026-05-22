# Phase1 5min Demo

## 前提

- `docker compose up --build` が完了している。
- `http://localhost:5173` と `http://localhost:8000/ready` が応答する。
- seed 済みの local demo account を使う。`admin@example.com` / `password` はローカルデモ用の dummy credential である。
- 外部 LLM は必須にしない。fake adapter で同じ流れを確認する。

## 5分シナリオ

| Time | Step | 操作 | 確認 |
|---|---|---|---|
| 0:00 | login as admin | UI で admin account に login する。 | admin 画面に移動できる。 |
| 0:30 | document upload / approve確認 | Admin Documents を開き、seed 文書と version 状態を見る。必要なら小さな `.md` を upload して approve する。 | active / archived version、job 状態、approve 後の状態を確認できる。 |
| 1:20 | chatで質問 | Chat で `What vector database is used by Phase1?` を送る。 | Qdrant に触れた回答が返る。 |
| 2:00 | citation panel確認 | assistant message の citation panel を開く。 | source label、snippet preview、page/section が出る。 |
| 2:30 | confidence確認 | confidence badge を見る。 | High / Medium / Low と score を確認できる。 |
| 3:00 | no_context質問 | seed 文書にない質問を送る。 | 通常回答と区別できる no context 系の扱いを確認できる。 |
| 3:40 | evaluation結果確認 | Admin Evaluation で `phase1_smoke` を 1件以上実行し、run detail を見る。 | case、metric、citation requirement を確認できる。 |
| 4:30 | MCP rag_ask実行 | `docs/demo/mcp_demo.md` の JSON-RPC 例で `tools/list` と `rag_ask` を実行する。 | local-only stdio server から citation 付き結果を確認できる。 |

## デモで話す要点

- Phase1 は local compose で再現する提出用の RAG stack である。
- CI は fake adapter を使い、重い model download を必須にしない。
- 回答は citation と confidence を伴うため、根拠確認と過信防止を両立する。
- MCP は外部公開ではなく local-only stdio で安全に確認する。
