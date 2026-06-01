# MCP Advanced RAG Tools

PR-38 exposes the Phase2 retrieval and evaluation capabilities through the
existing local MCP server. The server remains local-only, stdio-only, and
read-mostly. It does not expose upload, approve, archive, retry, destructive
evaluation operations, remote MCP transports, OAuth, Graph-RAG, OCR, S3, or
external operation agents.

## Scope

The MCP surface supports:

- `rag_search` with `strategy=dense|sparse|hybrid|agentic_router`
- `rag_search_hybrid` as a wrapper for `rag_search(strategy=hybrid)`
- `rag_search_agentic` as a wrapper for `rag_search(strategy=agentic_router)`
- `rag_ask` with default dense behavior and explicit `strategy=hybrid|agentic_router`
- `rag_ask_hybrid` as a wrapper for `rag_ask(strategy=hybrid)`
- `rag_ask_agentic` as a wrapper for `rag_ask(strategy=agentic_router)`
- `rag_ask_auto` as a wrapper for `rag_ask(strategy=llm_tool_orchestrator)`
- `rag_get_retrieval_trace` for safe retrieval trace summaries
- `rag_compare_strategies` for latest stored strategy comparison summaries
- `rag_get_evaluation_summary` for safe evaluation run summaries

The strategy comparison tool is read-only. It reads latest stored results and
does not create evaluation runs.

## Resources

- `rag://strategies`
- `rag://retrieval-runs/{retrieval_run_id}`
- `rag://evaluations/{evaluation_run_id}/summary`

Existing resources remain available:

- `rag://documents`
- `rag://documents/{logical_document_id}`
- `rag://jobs/{job_id}`
- `rag://evaluations/{evaluation_run_id}`

## Prompts

- `rag_hybrid_search_debug`
- `rag_agentic_answer_with_citations`
- `rag_strategy_comparison_review`

Prompt templates instruct MCP clients to avoid hidden raw chunks, raw prompts,
full context, storage paths, tokens, secrets, and admin writes.

## Settings

Defaults are safe and local-only:

```text
MCP_ENABLED=true
MCP_TRANSPORT=stdio
MCP_LOCAL_ONLY=true
MCP_ACTOR_MODE=mcp_local
MCP_ALLOW_WRITE_TOOLS=false
MCP_ENABLE_ADVANCED_RAG_TOOLS=true
MCP_ALLOWED_STRATEGIES=dense,sparse,hybrid,agentic_router,llm_tool_orchestrator
MCP_INCLUDE_TRACE_SUMMARY_DEFAULT=false
MCP_MAX_ANSWER_CHARS=4000
MCP_ALLOW_EVALUATION_RUN_CREATE=false
```

`MCP_ALLOW_WRITE_TOOLS=true`, remote transports, and evaluation run creation are
rejected by settings validation.

## Example Calls

Hybrid search:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "rag_search_hybrid",
    "arguments": {
      "query": "strategy evaluation fallback behavior",
      "top_k": 5,
      "rerank_top_n": 3,
      "include_trace_summary": true
    }
  }
}
```

Hybrid answer:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "rag_ask_hybrid",
    "arguments": {
      "question": "How does Hybrid RAG combine dense and sparse retrieval?",
      "top_k": 5,
      "rerank_top_n": 3,
      "include_trace_summary": true
    }
  }
}
```

Agentic answer:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "rag_ask_agentic",
    "arguments": {
      "question": "How does agentic_router choose a retrieval strategy?",
      "top_k": 5,
      "rerank_top_n": 3,
      "include_trace_summary": true
    }
  }
}
```

Auto answer:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "rag_ask_auto",
    "arguments": {
      "question": "Which retrieval strategy should answer this and why?",
      "top_k": 5,
      "rerank_top_n": 3,
      "include_trace_summary": true
    }
  }
}
```

Retrieval trace summary:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "rag_get_retrieval_trace",
    "arguments": {"retrieval_run_id": 123}
  }
}
```

Strategy comparison:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "rag_compare_strategies",
    "arguments": {
      "strategies": ["dense", "sparse", "hybrid", "agentic_router"],
      "mode": "latest_results"
    }
  }
}
```

## Redaction Rules

MCP outputs and resources include only bounded, safe data:

- source labels and snippets are truncated
- query trace uses `query_hash`, not raw query text
- query plan and router decision fields are allowlisted
- score, latency, fallback, sufficiency, and count fields are summarized
- evaluation summaries omit raw case prompts and full contexts
- `rag_ask_auto` uses the same PR-42 compressed tool result path as backend Auto

Never exposed:

- raw prompt
- full context
- raw chunk text
- raw Qdrant payload
- raw tool result payload
- raw job payload
- storage path
- token, secret, password, cookie, session, CSRF, credential values
- source URL query secrets

## Known Limits

- `rag_compare_strategies` reads latest stored results only.
- MCP does not start long-running evaluation jobs.
- Agentic tools use the existing bounded Phase2 agentic retrieval loop.
- Auto ask uses `llm_tool_orchestrator` and PR-42 deterministic tool result compression.
- Remote MCP, OAuth, Graph-RAG, OCR, and external operation agents are deferred.
