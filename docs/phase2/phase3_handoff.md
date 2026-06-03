# Phase3 Handoff

Phase2.5 finishes the local Advanced Retrieval, Context Engineering, MCP Auto, and local Kubernetes baseline handoff. Phase3 should build on this foundation without weakening the Phase2/Phase2.5 redaction and bounded-execution rules.

## Phase3 Candidate

Use this as the next PR candidate:

```text
Phase3 Design Baseline / Graph-RAG Planning
```

## Candidate Phase3 PRs

| Candidate | Scope |
|---|---|
| Phase3 Design Baseline / Graph-RAG Planning | Lock Graph-RAG, OCR, multimodal, provider, deployment, and auth boundaries before implementation. |
| Graph-RAG schema foundation | Add graph entities, relations, mentions, and graph index run tracking. |
| Entity / Relation Extraction | Extract safe graph candidates from approved document chunks. |
| Graph-aware Router | Add graph-aware strategy decisions and fallback rules. |
| Graph + Vector Hybrid | Combine graph paths with dense/hybrid retrieval evidence. |
| Graph Citation | Connect graph nodes/edges/path evidence to citation mapping. |
| OCR / PaddleOCR | Add scanned document/image OCR with confidence and region metadata. |
| Image upload | Extend approved upload and processing paths for images. |
| Multimodal citation UI | Navigate OCR/image regions without exposing raw private content. |
| External LLM provider | Add optional provider switching with explicit secret handling. |
| Online evaluation / A-B / Alerting | Add production evaluation, alerting, and monitoring loops. |

## Context Engineering Handoff

Phase3 must extend the Phase2.5 Context Engineering path instead of bypassing it:

- Graph paths must be subject to Context Budget.
- Graph evidence must be packed into Evidence Pack-like safe summaries.
- Graph node, edge, and path refs must preserve citation mapping without raw graph payload dumps.
- OCR regions must connect to source locator and citation mapping.
- Multimodal evidence must be considered by Tool Result Compression when exposed through orchestrator tools.
- Raw image content, raw OCR text, graph paths, full context, and prompt material must not be sent externally without an explicit policy decision.
- Trace and redaction policy must remain consistent across text, graph, OCR, image, and multimodal evidence.
- Viewer/admin debug boundaries must remain intact.

## Phase2.5 Extension Points

- `RetrievalStrategy` already has room for future strategy values.
- Retrieval trace JSON fields isolate query plan, strategy decision, score breakdown, latency, settings, Context Budget, Evidence Pack, and Tool Result Compression summaries.
- Evaluation metrics can be extended with graph path relevance, OCR accuracy, multimodal citation correctness, and online feedback metrics.
- Source locator DTOs can add OCR page/region, image bounding boxes, and graph node/edge references while retaining bounded previews.
- Advanced Office/web source metadata gives Phase3 a safe model for allowlisted metadata only.

## Security Handoff

- Keep external calls opt-in and secret-free by default.
- Do not store raw prompts, full context, raw chunk text, full fetched bodies, raw OCR text, raw image data, raw graph path dumps, tokens, credentials, or secret-like URL values in trace, evaluation artifacts, logs, UI, or MCP output.
- Preserve viewer/admin authorization differences for citation previews, document diff, and retrieval debug.
- Continue to reject or sandbox high-risk inputs such as SVG, macro-enabled Office files, private URL targets, embedded executable content, and unsafe redirects.
- Treat RAG context as evidence only, not as system instruction.

## Demo Handoff

Phase3 demos should start from the Phase2.5 final flow:

1. Start Docker Compose or local Kubernetes.
2. Ingest approved documents.
3. Ask with Auto in Chat UI.
4. Confirm Auto used strategy summary.
5. Inspect Retrieval Debug safe trace.
6. Confirm Context Budget, Evidence Pack, and Tool Result Compression panels.
7. Run MCP `rag_ask_auto` safely.
8. Navigate citations to bounded source previews.
9. Explain local Kubernetes baseline.

Only then add Graph/OCR/multimodal proof points.
