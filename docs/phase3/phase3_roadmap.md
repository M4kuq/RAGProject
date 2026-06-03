# Phase3 Roadmap

Phase3 expands the RAG system from advanced text retrieval into Graph-RAG, OCR, multimodal citation, provider expansion, production-oriented deployment planning, and online evaluation. PR-45 fixes the design baseline before implementation starts.

## PR Plan

| PR | Title | Primary outcome |
|---|---|---|
| PR-45 | Phase3 Design Baseline / Graph-RAG Planning | Fix architecture, schema draft, boundaries, acceptance, risk, and test plan. |
| PR-46 | Graph Schema / Graph Index Foundation | Add graph schema migration, repository skeleton, and deterministic graph index run state. |
| PR-47 | Entity / Relation Extraction Pipeline | Add extractor interface, worker job, deterministic test extractor, and safe source mapping. |
| PR-48 | Graph Retrieval Strategy / Graph-aware Router | Add graph lookup/traversal strategy and route graph-shaped queries. |
| PR-49 | Graph + Vector Hybrid Retrieval / Graph Citation | Combine graph paths with vector evidence and map graph citations to source chunks. |
| PR-50 | Graph Debug UI / Graph Evaluation | Add admin-safe graph panels and evaluation metrics for graph quality. |
| PR-51 | OCR Ingest / PaddleOCR / Scanned PDF | Add OCR ingest for scanned documents with region metadata. |
| PR-52 | Image Upload / Multimodal Metadata | Add image input lifecycle and safe metadata extraction. |
| PR-53 | Multimodal Citation Panel / OCR Region UI | Add bounded region navigation for OCR/image citations. |
| PR-54 | External LLM Provider Adapter | Add optional provider adapter with explicit export policy and redaction. |
| PR-55 | S3 Storage Adapter | Add optional object storage adapter and local-compatible test path. |
| PR-56 | OIDC / OAuth Authentication | Add external identity boundary while preserving viewer/admin roles. |
| PR-57 | AWS Deploy Foundation | Add cloud deployment foundation separately from local `k8s/local`. |
| PR-58 | Online Evaluation / A-B Evaluation / Alerting | Add production-like evaluation loops and alerting. |
| PR-59 | Phase3 Final Hardening / Production-like Demo | Finalize demo, acceptance, docs, smoke, and handoff. |

## Sequencing Rules

1. Graph schema and graph index state come before extraction or retrieval.
2. Extraction must preserve source chunk mapping before graph retrieval is exposed.
3. Graph retrieval must be available before Graph-aware Router can select it.
4. Graph Citation must validate node/edge/path mapping before user-facing graph evidence appears.
5. Graph Debug UI must show safe summaries only after backend trace contracts exist.
6. OCR and image work starts after text Graph-RAG has a stable citation model.
7. Production expansion stays optional and separate until local behavior is stable.

## Phase3 Exit Shape

Phase3 is complete when a third party can run a reproducible demo that shows:

- Graph-RAG answering graph-shaped queries.
- Graph + vector hybrid retrieval preserving source citations.
- Admin-safe graph debug and graph evaluation.
- OCR and multimodal citation proof points.
- Optional provider/storage/auth/deploy paths clearly separated from local defaults.
- No raw document text, raw chunk text, full context, PII, credential value, or secret value exposure.
