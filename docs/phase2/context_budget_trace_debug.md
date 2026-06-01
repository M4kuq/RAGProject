# PR-40 Context Budget / Trace / Debug Foundation

PR-40 adds a safe context-budget layer immediately before `/rag/ask` answer
generation. It applies to direct dense / hybrid ask, explicit
`agentic_router`, and `llm_tool_orchestrator` ask runs after retrieval,
RDB final check, rerank or final agentic selection, and retrieval-run item
persistence.

PR-40 does not compress, summarize, truncate, or semantically deduplicate
retrieved evidence. If an item does not fit, the initial policy drops the whole
item and records a safe reason. PR-41 consumes the selected refs and builds a
deterministic Evidence Pack before generation. Tool Result Compression is handed
off to PR-42 and occurs earlier, before the LLM orchestrator planner sees
retrieval tool results.

## ContextBudgetManager

`ContextBudgetManager` receives internal candidate references that may include
chunk text for local calculation. It emits only safe DTOs:

- `ContextItem`
- `ContextBudgetDecision`
- `ContextBudgetTrace`

The emitted trace never contains raw prompt, full context, raw chunk text,
snippets, raw tool outputs, PII, local paths, credentials, API keys, cookies,
session values, or secrets.

## Policy

Default policy:

| key | default |
|---|---:|
| `rag.context_budget.enabled` | `true` |
| `rag.context_budget.max_context_tokens` | `6000` |
| `rag.context_budget.reserve_answer_tokens` | `1000` |
| `rag.context_budget.max_context_items` | `12` |
| `rag.context_budget.max_tokens_per_item` | `1200` |
| `rag.context_budget.min_citation_candidates` | `1` |
| `rag.context_budget.drop_low_score_first` | `true` |
| `rag.context_budget.preserve_source_diversity` | `true` |
| `rag.context_budget.token_estimator` | `heuristic` |
| `rag.context_budget.store_debug_trace` | `true` |

Seeded `system_settings` are idempotent. Existing operator-defined settings are
not overwritten. Invalid environment values fail settings validation.

## Token Estimate

PR-40 uses a deterministic lightweight heuristic:

```text
estimated_tokens = ceil(char_count / 4)
```

The estimate is approximate and intentionally avoids heavyweight tokenizer
dependencies, model downloads, and network calls.

## Selection

The initial selection policy:

1. Preserve rerank order, final selected flag, and score order.
2. Treat rerank-selected items as citation candidates.
3. Prefer higher-ranked / higher-scored items.
4. Prefer one item per source before adding additional items from the same source.
5. Drop items beyond `max_context_items`.
6. Drop items that would exceed the effective context limit:
   `max_context_tokens - reserve_answer_tokens`.
7. Promote additional non-rerank-selected candidates only when needed to satisfy
   `min_citation_candidates` and the token/char budget allows.
8. Drop items whose own estimate exceeds `max_tokens_per_item`.

After context assembly applies the existing generation character cap, the
persisted selected refs and `retrieval_run_items.selected_flag` are synchronized
to the context refs actually passed to generation.

PR-40 does not truncate over-limit items. PR-41 owns bounded evidence text and
duplicate reduction in `retrieval_runs.context_compression_json`.

## Drop Reasons

Enum-like string values:

- `over_budget`
- `max_items_exceeded`
- `low_score`
- `duplicate_source`
- `duplicate_chunk`
- `missing_text`
- `unsafe_content`
- `not_selected_by_rerank`
- `source_diversity_limit`
- `unknown`

## Persistence

PR-40 adds nullable `retrieval_runs.context_budget_json` via migration
`0009_context_budget`.

Shape:

```json
{
  "schema_version": "phase2.context_budget.v1",
  "enabled": true,
  "budget": {
    "max_context_tokens": 6000,
    "reserve_answer_tokens": 1000,
    "max_context_items": 12,
    "max_tokens_per_item": 1200,
    "min_citation_candidates": 1,
    "token_estimator": "heuristic",
    "preserve_source_diversity": true,
    "drop_low_score_first": true
  },
  "usage": {
    "estimated_prompt_tokens": 12,
    "estimated_context_tokens": 420,
    "estimated_total_input_tokens": 432,
    "reserve_answer_tokens": 1000,
    "remaining_context_tokens": 5580,
    "budget_exhausted": false
  },
  "items": {
    "candidate_count": 5,
    "selected_count": 2,
    "dropped_count": 3,
    "citation_candidate_count": 2,
    "source_count": 2
  },
  "drop_reasons": {
    "not_selected_by_rerank": 3
  },
  "selected_item_refs": [],
  "dropped_item_refs": []
}
```

The persisted refs include bounded IDs, safe source labels, rank, score,
char-count, estimated-token count, and reason fields only.
`remaining_context_tokens` is calculated against the effective context limit
after reserving answer headroom.

## Debug UI

Admin Retrieval Debug renders a Context Budget panel when
`context_budget_json` is present. It displays:

- max context tokens
- estimated context tokens
- remaining context tokens
- selected / dropped counts
- drop reason counts
- citation candidate count
- source count and source breakdown
- selected and dropped safe item refs
- budget exhausted flag

The viewer chat UI does not render internal context budget debug data.

## Logging

Safe structured events:

- `rag.context_budget.applied`
- `rag.context_budget.exhausted`
- `rag.context_budget.skipped`

Allowed fields are run/request IDs, strategy labels, selected/execution
strategy, candidate/selected/dropped counts, estimated context tokens, remaining
context tokens, exhausted flag, and drop reason counts.

## Security

Do not store or display raw prompt, full context, raw chunk text, snippets, raw
tool outputs, PII, token values, secrets, credentials, cookies, sessions, or
local paths. Numeric token estimates are allowed and are not model tokens or
credentials.
