# PR-41 Evidence Pack / Retrieved Context Compression

PR-41 adds deterministic Evidence Pack construction between PR-40 context budget
selection and `/rag/ask` answer generation. It applies to dense, hybrid,
`agentic_router`, and `llm_tool_orchestrator` ask runs.

PR-41 compresses retrieved context only. PR-42 handles the separate intermediate
LLM orchestrator tool result compression boundary. PR-41 does not compress LLM
tool results, call external summarizers, require LLM summarization, add
Graph-RAG, OCR, multimodal retrieval, AWS/S3/OIDC, remote MCP, or external
operation agents.

## Components

- `EvidencePackBuilder` builds the final pack and safe trace.
- `ContextCompressor` performs deterministic duplicate removal and bounded
  evidence text creation.
- `EvidenceItem` is the internal item passed to generation.
- `EvidenceGroup` groups evidence by safe source group.
- `EvidencePackTrace` is the safe persisted summary.

The required mapping is preserved:

```text
EvidenceItem -> retrieval_run_item -> document_chunk -> citation
```

Every evidence item keeps `retrieval_run_item_id`, `document_chunk_id`, and
`local_citation_id` internally. Dropped items are not passed to citation
validation.

## Deterministic Compression

The compressor uses local deterministic rules only:

- exact duplicate removal by cleaned text hash
- normalized duplicate removal by casefolded whitespace-normalized text
- near-duplicate removal by token-set Jaccard overlap
- source grouping
- max items per source
- max total items
- bounded evidence text per item
- bounded total evidence text for generation

Default policy:

| key | default |
|---|---:|
| `rag.evidence_pack.enabled` | `true` |
| `rag.evidence_pack.max_items` | `12` |
| `rag.evidence_pack.max_items_per_source` | `4` |
| `rag.evidence_pack.max_chars_per_item` | `1200` |
| `rag.evidence_pack.max_total_chars` | `12000` |
| `rag.evidence_pack.near_duplicate_threshold` | `0.85` |
| `rag.evidence_pack.preserve_citation_candidates` | `true` |
| `rag.evidence_pack.group_by_source` | `true` |
| `rag.evidence_pack.store_debug_trace` | `true` |

The effective total evidence text cap is also bounded by
`generation_max_context_chars` so PR-41 does not expand the prompt beyond the
existing generation limit.

When `rag.evidence_pack.enabled` is `false`, the builder records a skipped safe
trace and bypasses Evidence Pack-specific item, source, and per-item caps. The
only remaining context bound is the pre-existing generation context character
limit.

## Persistence

PR-41 adds nullable `retrieval_runs.context_compression_json` via migration
`0010_context_compression`.

Shape:

```json
{
  "schema_version": "phase2.context_compression.v1",
  "enabled": true,
  "method": "deterministic_evidence_pack",
  "input": {
    "candidate_context_items": 20,
    "selected_context_items": 10,
    "input_estimated_tokens": 5200,
    "input_char_count": 18000
  },
  "output": {
    "evidence_group_count": 4,
    "evidence_item_count": 8,
    "output_estimated_tokens": 3100,
    "output_char_count": 10400,
    "compression_ratio": 0.6,
    "citation_candidate_count": 8
  },
  "drops": {
    "exact_duplicate_removed": 1,
    "near_duplicate_removed": 2
  },
  "evidence_groups": [],
  "evidence_item_refs": [],
  "dropped_item_refs": []
}
```

Trace refs include IDs, safe source labels, ranks, scores, char counts, token
estimates, compression method, and text hashes only. They do not include
`evidence_text_for_generation`.

## Debug UI

Admin Retrieval Debug renders an Evidence Pack panel when
`context_compression_json` is present. It displays:

- enabled flag and compression method
- input selected count and output evidence item count
- evidence group count
- compression ratio
- dropped duplicate counts
- max items per source
- evidence groups
- evidence item refs
- dropped evidence refs
- citation candidate count

Viewer chat UI does not render Evidence Pack internals.

## Security

Do not persist or display raw prompt, full context, raw chunk text,
`evidence_text_for_generation`, raw query, raw tool result, PII, tokens,
credentials, cookies, sessions, secrets, snippets inside compression trace, or
local paths. Admin debug shows bounded safe metadata only.

## PR-42 Handoff

PR-42 owns Tool Result Compression for LLM orchestrator tool outputs. PR-41 only
packs final retrieved context after context budget selection.
