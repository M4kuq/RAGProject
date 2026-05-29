# Phase2 Known Limitations

Phase2 is demo-ready, but it is not a production deployment. The following
limitations are intentional Phase2 boundaries or known areas for Phase3.

## Retrieval

- Sparse retrieval uses PostgreSQL full-text search. Japanese tokenization and
  mixed-language lexical matching are limited compared with a dedicated search
  engine or language-specific analyzer.
- Hybrid fusion uses the initial RRF/weighted fusion implementation. It is
  deterministic and traceable, but not yet tuned by large-scale offline
  experiments.
- Query Analyzer and Query Planner are deterministic rule-based components.
  They do not use a full LLM planner.
- Strategy Router is rule-based and bounded. It selects implemented strategies
  only and falls back to dense when needed.
- Agentic Retrieval Loop is intentionally small. Additional retrieval calls are
  bounded by settings, normally one initial call plus at most one fallback.
- Multi-query execution, metadata-filtered execution, and version-aware
  execution are planned or fallback-only in Phase2 unless explicitly wired by a
  later PR.

## Evaluation And Observability

- Strategy metrics are heuristic/deterministic and do not use a full
  LLM-as-a-judge pipeline.
- CI retrieval evaluation is not a mandatory pull-request gate. It is available
  through manual dispatch and optional schedule.
- The CI retrieval smoke uses real local retrieval. If model/cache/Qdrant
  prerequisites are unavailable, it reports a blocked artifact instead of using
  fake retrieval behavior.
- LangSmith trace export is optional and disabled/no-op by default.
- SentenceTransformers experiments are local opt-in. Dry-run mode is the
  default and does not download models.

## Import And Documents

- Excel and PowerPoint support is limited to text extraction and safe structural
  metadata. Legacy `.xls` / `.ppt`, macro-enabled files, embedded objects,
  speaker notes, OCR, and visual layout understanding are not supported.
- HTML/XML import extracts safe text and structural paths. JavaScript rendering,
  headless browser execution, SVG import, and interactive web content are not
  supported.
- URL ingest fetches one URL only. It is not a crawler, sitemap importer, or
  recursive web ingester.
- URL ingest rejects auth/userinfo URLs and private/internal targets through the
  SSRF guard. It does not support cookies, login flows, or authenticated fetch.
- Document diff is lightweight and bounded. It does not render PDF pages, DOCX
  layouts, PPTX slides, or full unbounded text diffs.
- Citation navigation shows a safe source locator and bounded preview. It does
  not expose raw full chunk text or storage paths.

## Security And Privacy

- Docs, traces, logs, artifacts, and UI must not include raw prompts, full
  context, raw chunk text, PII, tokens, credentials, API keys, or secrets.
- Destructive commands such as `docker compose down -v`, database reset/drop, or
  force push are not part of the normal demo or smoke flow.
- External export, external API usage, and heavy model downloads require
  explicit opt-in outside the default flow.

## Phase3 Deferred Scope

- Graph-RAG and graph-aware routing.
- OCR, image upload, multimodal retrieval, and OCR region citation UI.
- AWS deployment, S3 storage, OIDC/OAuth, and production-grade secrets
  management.
- Online evaluation, A/B evaluation, alerting, and production trace sampling.
