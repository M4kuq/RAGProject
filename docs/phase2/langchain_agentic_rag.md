# LangChain Agentic RAG

`langchain_agentic` adds a LangChain-based Agentic-RAG ask path that can be
compared with the existing in-house `llm_tool_orchestrator` mode.

## Positioning

| Mode | Strategy | Orchestrator | Retrieval tools | Generation |
|---|---|---|---|---|
| Auto / in-house Agentic RAG | `llm_tool_orchestrator` | Project-native bounded tool loop | dense, sparse, hybrid | Existing answer generator |
| LangChain Agentic RAG | `langchain_agentic` | LangChain `RunnableLambda` planner + `StructuredTool` wrappers | dense, sparse, hybrid | Existing answer generator |

Both modes keep the same safety boundary:

- retrieval-only tools
- bounded tool/search calls
- compressed tool results
- no admin/write tools
- no raw prompt, raw chunk text, or full context in trace output
- final answer generation still goes through the existing citation-aware RAG path

## Runtime Surface

Explicit ask request:

```json
{
  "message": "Summarize the indexed policy evidence",
  "strategy": "langchain_agentic",
  "top_k": 8,
  "rerank_top_n": 5
}
```

Chat UI exposes the mode as `LangChain Agentic`.

MCP exposes:

- `rag_ask_langchain_agentic`
- `rag_ask` with `strategy=langchain_agentic`
- `rag_compare_strategies` with `langchain_agentic` beside
  `llm_tool_orchestrator`

Evaluation runs can compare:

```json
{
  "strategies": ["llm_tool_orchestrator", "langchain_agentic"]
}
```

## Trace Fields

LangChain runs are stored as `retrieval_runs.strategy_type =
"langchain_agentic"`.

Safe trace and summary fields include:

- `orchestrator_provider = "langchain"`
- `tool_call_count`
- `search_call_count`
- `tools_used`
- `finalize_called`
- `budget_exhausted`
- `timeout_exceeded`
- `langchain_agentic_ms`
- `langchain_planning_ms`
- `langchain_tool_execution_ms`

`retrieval_run_items.retrieval_source` remains the concrete executed retrieval
source, such as `hybrid`. `score_breakdown_json.retrieval_source` identifies the
overall orchestration mode as `langchain_agentic`.

## Checks

Focused backend checks:

```powershell
cd backend
python -m pytest tests/test_rag_ask.py tests/test_evaluations.py tests/test_mcp_server.py tests/test_rag_trace.py tests/test_rag_strategy_schema.py -q
python -m ruff check .
```

Frontend checks:

```powershell
cd frontend
npm test
npm run build
```
