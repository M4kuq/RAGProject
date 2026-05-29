# LLM Tool-Calling Retrieval Orchestrator

PR-39 adds an opt-in RAG mode named `llm_tool_orchestrator`. It is separate from
the existing rule-based `agentic_router`.

## Modes

| UI label | Backend strategy | Behavior |
|---|---|---|
| Normal RAG | `dense` | Dense vector retrieval, rerank, answer generation |
| Hybrid RAG | `hybrid` | Dense + sparse retrieval with fusion, then answer generation |
| Agentic Router | `agentic_router` | Rule-based query plan and bounded fallback retrieval |
| LLM Agentic RAG | `llm_tool_orchestrator` | LLM chooses retrieval-only tools in a bounded loop |

## Retrieval-Only Tools

The orchestrator can call only these tools:

- `dense_search`
- `sparse_search`
- `hybrid_search`
- `inspect_retrieval_trace`
- `finalize_answer`

It cannot upload, archive, approve, retry jobs, run direct DB queries, access the
file system, fetch URLs, or operate external systems. The final answer is still
generated through the existing `/rag/ask` generation and citation pipeline after
`finalize_answer`.

## Budgets

The loop is bounded by settings:

- `LLM_ORCHESTRATOR_MAX_TOOL_CALLS`
- `LLM_ORCHESTRATOR_MAX_SEARCH_CALLS`
- `LLM_ORCHESTRATOR_TIMEOUT_SECONDS`
- `LLM_ORCHESTRATOR_MAX_QUERY_CHARS`
- `LLM_ORCHESTRATOR_MAX_TOOL_RESULT_ITEMS`
- `LLM_ORCHESTRATOR_MAX_SNIPPET_CHARS`

Repeated identical search tool calls are blocked. If the loop reaches its budget
without `finalize_answer`, `/rag/ask` returns the existing `422 no_context_found`
contract and does not create an assistant placeholder.

## Trace And Redaction

`retrieval_runs.strategy_type` is `llm_tool_orchestrator`. The trace stores safe
counts and decisions only:

- tool call count
- search call count
- tool names
- budget and timeout flags
- repeated query flag
- finalize flag
- item counts

The trace does not store raw prompts, full context, raw chunk text, full tool
payloads, PII, tokens, or secrets. Tool results shown to the planning LLM contain
bounded snippets, source labels, score summaries, and chunk ids only.

## Local Use

In the Chat UI, choose **LLM Agentic RAG** from the RAG mode selector. For local
LLM behavior, use the existing `GENERATION_PROVIDER=lmstudio` path. CI and tests
use deterministic local planning and do not require external API keys.

Direct API example:

```json
{
  "chat_session_id": 1,
  "client_message_id": "demo-llm-agentic-1",
  "message": "Compare dense and hybrid retrieval behavior.",
  "strategy": "llm_tool_orchestrator",
  "top_k": 10,
  "rerank_top_n": 5
}
```

Do not include secrets, production customer text, or raw retrieved chunks in demo
notes or issue comments.
