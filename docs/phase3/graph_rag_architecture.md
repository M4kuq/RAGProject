# Graph-RAG Architecture

PR-46 starts the implementation path by adding the graph schema and index run foundation. It keeps Phase3 aligned with the PR-45 design baseline while avoiding retrieval/extraction implementation.

## Phase2 Relationship

Phase2 and Phase2.5 remain the active retrieval path:

- dense retrieval
- sparse retrieval
- hybrid retrieval
- agentic router
- LLM tool-calling Auto
- Context Budget
- Evidence Pack
- Tool Result Compression

Graph-RAG must plug into this stack instead of bypassing it.

## PR-46 Boundary

Implemented:

- graph tables
- ORM models
- DTOs
- repository methods
- index run lifecycle skeleton
- future job type constant
- disabled settings defaults
- tests and docs

Not implemented:

- entity/relation extraction
- graph retrieval
- graph-aware router
- graph citation generation
- graph debug UI
- OCR/multimodal
- production/AWS expansion

## Future Integration

PR-47 connects extraction to `GraphIndexService`.

PR-48 adds graph lookup/traversal and routing decisions.

PR-49 combines graph paths with vector evidence and citation mapping.

All future graph results must pass through Context Budget and Evidence Pack before answer generation. Tool outputs for future graph tools must be compressed and bounded before planner visibility.

## Evidence Policy

Graph evidence is represented by IDs, hashes, source refs, labels, confidence, and score summaries. Raw document text, raw chunk text, full context, raw prompt material, PII, token values, and secret values are not persisted to graph tables or exposed through API/debug output.
