# LLM Tool-Calling Retrieval Orchestrator

PR-39 では、`llm_tool_orchestrator` という opt-in の RAG モードを追加します。
これは既存の `agentic_router` を置き換えるものではなく、別の上位モードです。`agentic_router` は `rule_based` または bounded LLM planner mode で動作します。

## モードの違い

| UI 表示 | Backend strategy | 挙動 |
|---|---|---|
| Normal RAG | `dense` | dense ベクトル検索、rerank、回答生成 |
| Hybrid RAG | `hybrid` | dense + sparse 検索を fusion し、rerank、回答生成 |
| Agentic Router | `agentic_router` | query plan と `rule_based` または bounded LLM planner による strategy routing |
| LLM Agentic RAG | `llm_tool_orchestrator` | LLM が retrieval-only tool を bounded に選択し、十分なら回答生成へ進む |

## Retrieval-only tools

orchestrator が呼び出せる tool は retrieval-only の以下だけです。

- `dense_search`
- `sparse_search`
- `hybrid_search`
- `inspect_retrieval_trace`
- `finalize_answer`

upload、archive、approve、job retry、DB 直接操作、ファイルシステム操作、URL fetch、外部システム操作はできません。
`finalize_answer` は「検索根拠が十分なので既存の回答生成パイプラインへ進む」という意思決定だけを表します。
最終回答の生成、citation、confidence 保存は既存の `/rag/ask` パイプラインで行います。

## Budget

tool loop は以下の設定で必ず上限を持ちます。

- `LLM_ORCHESTRATOR_MAX_TOOL_CALLS`
- `LLM_ORCHESTRATOR_MAX_SEARCH_CALLS`
- `LLM_ORCHESTRATOR_TIMEOUT_SECONDS`
- `LLM_ORCHESTRATOR_MAX_QUERY_CHARS`
- `LLM_ORCHESTRATOR_MAX_TOOL_RESULT_ITEMS`
- `LLM_ORCHESTRATOR_MAX_SNIPPET_CHARS`

同一 strategy + 同一 query の検索 tool call は repeated query としてブロックします。
budget 超過、timeout、検索結果なし、または `finalize_answer` なしで終了した場合は、既存契約に合わせて
`422 no_context_found` を返し、失敗した assistant placeholder は作成しません。

## Trace と redaction

`retrieval_runs.strategy_type` は `llm_tool_orchestrator` になります。
trace には安全な summary だけを保存します。

- tool call count
- search call count
- 使用 tool 名
- budget / timeout / repeated query flag
- finalize flag
- item count
- latency summary

保存しないもの:

- raw prompt
- full context
- raw chunk text
- raw tool payload
- PII
- token
- secret

planning LLM に渡す tool result も、bounded snippet、source label、score summary、chunk id だけです。
raw chunk full text や full context は渡しません。

## UI からの使い方

Chat 画面の RAG mode selector で **LLM Agentic RAG** を選択します。

通常確認する項目:

1. Chat 画面で **LLM Agentic RAG** を選ぶ。
2. safe synthetic query を送る。
3. 根拠が十分なら citations / confidence 付きで回答が返る。
4. 根拠不足なら `no_context_found` として表示され、assistant placeholder は作られない。
5. Admin の Retrieval Debug で `llm_tool_orchestrator` run を開き、tool call count、search call count、finalize flag、budget flag、latency summary を確認する。

## API 例

```json
{
  "chat_session_id": 1,
  "client_message_id": "demo-llm-agentic-1",
  "message": "dense と hybrid の検索挙動を比較してください",
  "strategy": "llm_tool_orchestrator",
  "top_k": 10,
  "rerank_top_n": 5
}
```

demo note、issue comment、ログには、secret、実顧客文書、raw retrieved chunk、full context を貼らないでください。
