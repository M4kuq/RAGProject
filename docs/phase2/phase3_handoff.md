# Phase3 Handoff

Phase2.5 finishes the local Advanced Retrieval, Context Engineering, MCP Auto, and local Kubernetes baseline handoff. PR-45 starts Phase3 by fixing the Graph-RAG design baseline under [`docs/phase3/README.md`](../phase3/README.md).

Phase3 should build on this foundation without weakening the Phase2/Phase2.5 redaction and bounded-execution rules.

## Phase3 Start PR

Use this as the active Phase3 start PR:

```text
PR-45 Phase3 Design Baseline / Graph-RAG Planning
```

Design baseline entry points:

- [Phase3 README](../phase3/README.md)
- [Phase3 Roadmap](../phase3/phase3_roadmap.md)
- [Graph-RAG Architecture](../phase3/graph_rag_architecture.md)
- [Graph Schema Draft](../phase3/graph_schema_draft.md)
- [Security / Redaction Policy](../phase3/security_redaction_policy.md)

## Candidate Phase3 PRs

| Candidate | Scope |
|---|---|
| PR-45 Phase3 Design Baseline / Graph-RAG Planning | Lock Graph-RAG, OCR, multimodal, provider, deployment, auth, evaluation, and redaction boundaries before implementation. |
| PR-46 Graph Schema / Graph Index Foundation | Add graph entities, relations, mentions, graph index runs, and retrieval path tracking. |
| PR-47 Entity / Relation Extraction Pipeline | Extract safe graph candidates from approved document chunks. |
| PR-48 Graph Retrieval Strategy / Graph-aware Router | Add graph-aware strategy decisions and fallback rules. |
| PR-49 Graph + Vector Hybrid / Graph Citation | Combine graph paths with dense/hybrid retrieval evidence and source citations. |
| PR-50 Graph Debug UI / Graph Evaluation | Add admin-safe graph panels and graph quality metrics. |
| PR-51 OCR / PaddleOCR | Add scanned document/image OCR with confidence and region metadata. |
| PR-52 Image Upload | Extend approved upload and processing paths for images. |
| PR-53 Multimodal Citation UI | Navigate OCR/image regions without exposing raw private content. |
| PR-54 External LLM Provider | Add optional provider switching with explicit secret/export handling. |
| PR-55 S3 Storage Adapter | Add optional object storage path. |
| PR-56 OIDC / OAuth | Add external identity while preserving viewer/admin boundaries. |
| PR-57 AWS Deploy Foundation | Add cloud deployment foundation outside local `k8s/local`. |
| PR-58 Online Evaluation / A-B / Alerting | Add production evaluation, alerting, and monitoring loops. |
| PR-59 Phase3 Final Hardening | Finalize demo, acceptance, smoke, docs, and handoff. |

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
- Do not store raw prompts, full context, raw chunk text, full fetched bodies, raw OCR text, raw image data, raw graph path dumps, credential values, or secret-like URL values in trace, evaluation artifacts, logs, UI, or MCP output.
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
