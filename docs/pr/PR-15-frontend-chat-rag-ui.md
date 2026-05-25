# PR-15 Frontend Chat RAG UI

## Scope

PR-15 は PR-14 の `/api/v1/rag/ask` 契約を前提に、React の ChatPage から RAG 回答を送信・表示するための最小 UI 統合を追加する。

実装範囲:

- ChatPage から `/api/v1/rag/ask` を呼び出す
- frontend で `client_message_id` を送信ごとに生成する
- optimistic user message と assistant loading を表示する
- 成功時に assistant answer、citations、confidence、old source badge を表示する
- `meta.replayed=true` を通常の assistant answer として表示する
- `request_in_progress`、`no_context_found`、readonly 系エラーを user-safe message に変換する
- archived / temporary expired session で composer を disabled にする
- temporary session の banner / badge を表示する

対象外:

- backend API 追加
- RAG pipeline / citation / confidence 算出ロジック変更
- admin document UI、evaluation UI、streaming、feedback、rich markdown 対応

## Security notes

- state-changing request は既存 `apiFetch` により `X-CSRF-Token` を付与する。
- assistant answer / citation snippet は React text rendering のみで表示し、HTML として描画しない。
- UI error は error code/status から固定文言へ変換し、raw details は表示しない。
- viewer/admin の通常 chat UI では `retrieval_run_id` や `linked_retrieval_run_id` を表示しない。
- citation label / snippet は whitespace normalization と truncation を通して表示する。
