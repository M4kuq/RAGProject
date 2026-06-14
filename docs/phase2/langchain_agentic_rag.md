# LangChain / LangGraph Agentic RAG

`langchain_agentic` and `langgraph_agentic` add framework-based Agentic-RAG ask
paths that can be compared with the existing in-house
`llm_tool_orchestrator` mode.

## Positioning

| Mode | Strategy | Orchestrator | Retrieval tools | Generation |
|---|---|---|---|---|
| Auto / in-house Agentic RAG | `llm_tool_orchestrator` | Project-native bounded tool loop | dense, sparse, hybrid | Existing answer generator |
| LangChain Agentic RAG | `langchain_agentic` | LangChain `RunnableLambda` planner + `StructuredTool` wrappers | dense, sparse, hybrid | Existing answer generator |
| LangGraph Agentic RAG | `langgraph_agentic` | LangGraph `StateGraph` planner/executor loop + the same `StructuredTool` wrappers | dense, sparse, hybrid | Existing answer generator |

Both modes keep the same safety boundary:

- retrieval-only tools
- bounded tool/search calls
- compressed tool results
- no admin/write tools
- no raw prompt, raw chunk text, or full context in trace output
- final answer generation still goes through the existing citation-aware RAG path

## LLM Planner Mode

When `ROUTER_MODE=llm`, both framework-based modes reuse the same bounded JSON
planner as `agentic_router` to choose the next retrieval tool. The planner sees
only the user query, safe query/tool summaries, available strategies, attempted
strategies, and remaining search budget. It does not receive raw chunks, full
tool output text, prompts, or secret-like values.

If the planner is unavailable, times out, returns invalid JSON, selects an
unavailable/already-attempted strategy, or asks to finalize before any useful
tool result exists, the orchestrator falls back to the existing deterministic
tool order. In LM Studio deployments, the answer model can remain larger while
the planner uses the lighter `ROUTER_LLM_PLANNER_MODEL_NAME` override.

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

Chat UI exposes the modes as `LangChain Agentic` and `LangGraph Agentic`.

MCP exposes:

- `rag_ask_langchain_agentic`
- `rag_ask_langgraph_agentic`
- `rag_ask` with `strategy=langchain_agentic`
- `rag_ask` with `strategy=langgraph_agentic`
- `rag_compare_strategies` with `langchain_agentic` and
  `langgraph_agentic` beside `llm_tool_orchestrator`

Evaluation runs can compare:

```json
{
  "strategies": ["llm_tool_orchestrator", "langchain_agentic", "langgraph_agentic"]
}
```

## Trace Fields

LangChain runs are stored as `retrieval_runs.strategy_type =
"langchain_agentic"`.

LangGraph runs are stored as `retrieval_runs.strategy_type =
"langgraph_agentic"`.

Safe trace and summary fields include:

- `orchestrator_provider = "langchain"`
- `tool_call_count`
- `search_call_count`
- `tools_used`
- `finalize_called`
- `budget_exhausted`
- `timeout_exceeded`
- `llm_planner_used`
- `planner_provider`
- `planner_model`
- `planner_action`
- `planner_selected_strategy`
- `planner_fallback_reason`
- `planner_events`
- `langchain_agentic_ms`
- `langchain_planning_ms`
- `langchain_tool_execution_ms`
- `langgraph_agentic_ms`
- `langgraph_planning_ms`
- `langgraph_tool_execution_ms`

`retrieval_run_items.retrieval_source` remains the concrete executed retrieval
source, such as `hybrid`. `score_breakdown_json.retrieval_source` identifies the
overall orchestration mode as `langchain_agentic` or `langgraph_agentic`.

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
