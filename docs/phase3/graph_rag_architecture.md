# Graph-RAG Architecture

## Purpose

Graph-RAG adds an entity and relation layer on top of the existing document chunk retrieval stack. It is meant to answer questions where the useful evidence is not just the nearest chunk, but a path across entities, relations, document versions, and source chunks.

Graph-RAG is not a replacement for dense, sparse, hybrid, agentic router, or Auto. It is an additional retrieval family that must preserve the Phase2.5 Context Engineering and citation invariants.

## Relationship To Phase2 Retrieval

Phase2 retrieval is source chunk centered:

```text
query -> dense/sparse/hybrid/agentic/auto -> retrieval_run_items -> citations
```

Phase3 graph retrieval adds a graph layer but still returns chunk-backed evidence:

```text
query -> graph entity/relation/path retrieval -> graph path refs -> source chunk refs -> retrieval_run_items -> citations
```

The graph can rank and explain evidence, but final answer grounding remains tied to `document_chunks` through `retrieval_run_items` and citations.

## Query Types Graph-RAG Should Help

- Multi-hop questions that require connecting two or more entities.
- Relation-aware questions such as ownership, dependency, cause, compatibility, or sequence.
- Entity-centric questions that ask for everything known about a named concept.
- Comparison questions between entities, versions, systems, or decisions.
- Traceability questions that ask why a decision, document, or component relates to another.
- Graph path evidence questions where the explanation is a path rather than one passage.

## Roles By Retrieval Family

| Retrieval family | Strength | Phase3 role |
|---|---|---|
| Dense | semantic similarity | fallback and broad semantic recall |
| Sparse | exact terms and identifiers | keyword/entity labels and error codes |
| Hybrid | balanced text retrieval | default companion to graph evidence |
| Agentic Router | bounded rule-based strategy routing | fallback when graph confidence is low |
| LLM Tool Orchestrator / Auto | retrieval-only tool calling | future graph search tool user, still bounded |
| Graph | entity/relation/path reasoning | multi-hop and relation-aware evidence |
| Graph + Vector Hybrid | path plus supporting text | preferred user-facing graph answer path |

## Auto / LLM Orchestrator Integration

PR-45 does not add a graph tool. Future PRs should add a retrieval-only `graph_search` tool after graph retrieval is tested. The orchestrator may call it only within these bounds:

- No upload, archive, approve, retry, deployment, or admin mutation tools.
- Max graph traversal budget from settings.
- Tool Result Compression before planner-visible output.
- Safe tool result fields only: entity refs, relation refs, path refs, scores, source chunk IDs, hashes, counts, and reason codes.
- No raw graph evidence text or full path payload dump.

## Context Engineering Integration

| Phase2.5 component | Graph-RAG integration |
|---|---|
| Context Budget | Graph path candidates consume context budget before generation. |
| Evidence Pack | Graph evidence is packed into safe evidence groups while preserving source mapping. |
| Tool Result Compression | Graph tool output is compressed before Auto planner visibility. |
| Retrieval Debug | Admin sees safe graph summaries, path refs, score breakdowns, and warnings. |
| MCP `rag_ask_auto` | Future graph selection returns safe strategy summary only. |

## MCP Extension Policy

Future MCP tools can expose graph retrieval only as read-only local tools. Candidate tools:

- `rag_graph_search`
- `rag_graph_explain_path`
- `rag_ask_graph_auto`

They must not return raw document text, raw chunk text, full context, credential values, or raw graph payload dumps.

## API Design Delta

PR-45 documents candidate API deltas only. Future PRs may add:

- `strategy=graph` and `strategy=graph_hybrid` for `/api/v1/rag/search` and `/api/v1/rag/ask`.
- Admin retrieval debug fields for graph path summaries.
- Read-only graph inspection endpoints for admins.
- Evaluation result fields for graph path relevance and graph citation coverage.

No API implementation is included in PR-45.

## In Scope For Phase3

- Graph schema and index run state.
- Entity and relation extraction from approved document chunks.
- Graph traversal and neighborhood expansion.
- Graph-aware routing and fallback.
- Graph + vector hybrid retrieval.
- Graph path citations and validation.
- Graph Debug UI and Graph Evaluation.
- OCR and multimodal boundaries after Graph-RAG baseline.

## Out Of Scope For PR-45

- Runtime graph tables.
- Alembic migration.
- Extractor implementation.
- Graph database integration.
- Graph retrieval implementation.
- OCR, image upload, multimodal UI.
- AWS/S3/OIDC/external provider implementation.
