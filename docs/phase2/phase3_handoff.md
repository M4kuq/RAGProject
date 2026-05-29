# Phase3 Handoff

Phase2 finishes the local Advanced Retrieval, agentic control, evaluation,
observability, advanced import, and document navigation foundation. Phase3
should build on that foundation without weakening the Phase2 redaction and
bounded-execution rules.

## Candidate Phase3 PRs

| Candidate | Scope |
|---|---|
| PR-38 Phase3 Design Baseline / Production Architecture | Lock Graph, OCR, multimodal, deployment, and auth boundaries. |
| Graph-RAG schema foundation | Add graph entities, relations, mentions, and graph index run tracking. |
| Entity / relation extraction pipeline | Extract safe graph candidates from approved document chunks. |
| Graph retrieval and graph-aware router | Add graph strategy values and router decision rules. |
| Graph + vector hybrid | Combine graph paths with dense/hybrid retrieval evidence. |
| OCR ingest | Add scanned PDF/image OCR with confidence and region metadata. |
| Image upload / multimodal metadata | Extend document modality and safe source locators. |
| Multimodal citation UI | Navigate OCR/image regions without exposing raw private content. |
| External LLM provider adapter | Optional provider switching with explicit secret handling. |
| S3 storage adapter | Replace or supplement local storage with object storage. |
| OIDC / OAuth | Add external identity provider support. |
| AWS deploy foundation | Production-like infrastructure and deployment workflow. |
| Online evaluation / A-B / alerting | Production evaluation and monitoring loop. |

## Phase2 Extension Points

- `RetrievalStrategy` is intentionally extensible for graph, graph-hybrid, and
  multimodal strategies.
- Retrieval trace JSON fields already isolate query plan, strategy decision,
  score breakdown, latency, settings, and safe summaries.
- Evaluation metrics can be extended with graph path relevance, OCR accuracy,
  multimodal citation correctness, and online feedback metrics.
- `document_chunks.modality` currently remains text-focused; Phase3 can expand
  modality values for OCR text, image captions, tables, and graph mentions.
- Source locator DTOs can add OCR page/region, image bounding boxes, and graph
  node/edge references while retaining bounded previews.
- Advanced import metadata for Office and web sources gives Phase3 a safe model
  for allowlisted metadata only.

## Security Handoff

- Keep external calls opt-in and secret-free by default.
- Do not store raw prompts, full context, raw chunk text, full fetched bodies,
  OCR images, tokens, credentials, or secret-like URL values in trace,
  evaluation artifacts, logs, or UI.
- Preserve viewer/admin authorization differences for citation previews and
  document diff.
- Continue to reject or sandbox high-risk inputs such as SVG, macro-enabled
  Office files, private URL targets, and embedded executable content.
- Treat RAG context as evidence only, not as system instruction.

## Demo Handoff

Phase3 demos should start from the Phase2 final flow:

1. Ingest approved documents.
2. Search with dense/sparse/hybrid/agentic_router.
3. Inspect Retrieval Debug trace.
4. Run strategy evaluation and failure promotion.
5. Navigate citations to bounded source previews.
6. Compare document versions.

Only then add Graph/OCR/multimodal proof points.
