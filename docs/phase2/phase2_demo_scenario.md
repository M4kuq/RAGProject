# Phase2 Demo Scenario

This scenario is the Phase2 handoff demo path. It is designed for a 5-10
minute walkthrough on a local Docker Compose environment. It uses safe sample
queries only and must not paste real user prompts, private documents, secrets,
or raw retrieved context into the demo notes.

## Preconditions

- Docker Compose services are running from the repository root.
- Database migrations and seed data have completed.
- The admin user can sign in with the local demo account documented for local
  development.
- Seeded documents and the `phase2_strategy_smoke` evaluation dataset are
  available.
- Optional integrations such as LangSmith and SentenceTransformers local
  experiments remain disabled unless the presenter explicitly opts in.

## Demo Flow

1. **Start and health check**
   - Show `docker compose config` and the running backend/frontend services.
   - Open `http://localhost:5173`.
   - Confirm `/health` and `/ready` are healthy.

2. **Admin sign-in**
   - Sign in as the local seeded admin.
   - Avoid showing any real credential or `.env` value on screen.

3. **Document ingest status**
   - Open Admin Documents.
   - Show ready versions, chunk counts, and source labels.
   - Point out supported Phase2 imports: `.xlsx`, `.pptx`, `.html`, `.htm`,
     `.xml`, and single URL ingest.

4. **Dense / sparse / hybrid comparison**
   - Open Retrieval Debug.
   - Run the same safe query with `dense`, `sparse`, and `hybrid`.
   - Show score breakdown, retrieval source, latency, and selected item counts.

5. **Agentic router search**
   - Run `strategy=agentic_router`.
   - Show query plan, router decision, execution strategy, fallback state,
     sufficiency summary, retrieval call count, and latency.

6. **Retrieval Debug UI v2**
   - Open a retrieval run detail.
   - Confirm `query_plan_json`, `strategy_decision_json`,
     `retrieval_settings_json`, `score_breakdown_json`, and
     `latency_breakdown_json` are visible only as safe summaries.

7. **Strategy evaluation**
   - Open Evaluations.
   - Show an existing run or create a small manual run with
     `dense,hybrid,agentic_router`.
   - Show recall, MRR, citation coverage, no-context rate, p95 latency, and
     agentic metrics.

8. **Failure promotion**
   - Show failure candidates from an evaluation run.
   - Promote a small filtered set into an active dataset.
   - Explain idempotency: repeated promotion should be skipped/already exists.

9. **CI retrieval evaluation**
   - Open `.github/workflows/retrieval-eval-smoke.yml`.
   - Explain manual `workflow_dispatch`, optional schedule, warn/fail mode,
     JSON/Markdown artifacts, and blocked artifacts for missing local model or
     Qdrant prerequisites.

10. **Optional observability**
    - Open the LangSmith optional adapter docs.
    - Explain default no-op behavior and that external export requires explicit
      settings and a secret outside this repository.

11. **SentenceTransformers experiment harness**
    - Run or show dry-run mode:
      `scripts/run_retrieval_model_experiment.ps1 -Mode dry-run -DownloadPolicy never`
    - Explain that local mode is opt-in and no model is downloaded by default.

12. **Advanced import**
    - Show Office import metadata for a spreadsheet or presentation.
    - Show HTML/XML/URL source metadata and SSRF guard constraints.
    - Emphasize no crawler, no JavaScript rendering, no OCR.

13. **Document diff and citation navigation**
    - Open Document Detail > Version Compare.
    - Compare two versions and show metadata/chunk diff counts with bounded
      previews.
    - Open a chat answer citation, click View source, and show the bounded
      source locator.

## Safe Demo Queries

Use short synthetic queries against the seeded demo corpus. Do not use
customer data, real credentials, or private document text.

| Query type | Example |
|---|---|
| Keyword-heavy | `RAGProject Qdrant sparse retrieval settings` |
| Semantic | `How does the system choose retrieval evidence?` |
| Comparison | `Compare dense and hybrid retrieval behavior.` |
| Version-specific | `What changed in the newer policy version?` |
| No context | `What is the weather on Mars today?` |
| Office metadata | `Which sheet or slide mentions retrieval strategy?` |
| URL source | `Which imported web page describes SSRF guard behavior?` |

## Presenter Notes

- Do not open `.env` or print environment variables.
- Do not show raw retrieval payloads, raw chunks, full prompts, or full context.
- Use bounded previews from the UI rather than database dumps.
- Mention that Phase3 covers Graph-RAG, OCR, multimodal UI, AWS/S3, OIDC, and
  online evaluation.
