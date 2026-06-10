# Graph Evaluation Design

PR-45 defines evaluation metrics and CI strategy for future graph work.

## Evaluation Areas

| Area | Metric examples |
|---|---|
| Entity extraction accuracy | precision, recall, F1, alias merge accuracy |
| Relation extraction accuracy | relation precision, relation recall, hallucinated relation rate |
| Graph path relevance | path relevance, hop correctness, source support coverage |
| Multi-hop QA accuracy | answer correctness, required-hop coverage, no-context correctness |
| Graph citation coverage | node citation coverage, edge citation coverage, path citation validation |
| Strategy comparison | dense vs hybrid vs agentic_router vs graph vs graph_hybrid |
| Graph-only vs graph + vector | answer quality, citation coverage, latency, context usage |

## Dataset Expansion

Future datasets should include safe synthetic or demo cases with:

- explicit entity names
- relation labels
- multi-hop expected paths
- version-specific comparisons
- no-context graph queries
- OCR/image cases after PR-51

Datasets must not include PII, raw private documents, credential values, or secret values.

## Failure Promotion

A graph failure should be promoted to a tracked failure class when:

- relation hallucination creates unsupported edges
- graph path lacks source chunk support
- graph answer cites chunks outside selected retrieval run items
- graph traversal exceeds budget
- stale graph is used without version-aware mode
- graph debug payload contains unsafe keys

## CI Evaluation Expansion

PR-46/PR-47 add small deterministic tests. Later PRs can add optional evaluation jobs:

- schema and repository unit tests
- deterministic extractor fixture tests
- graph traversal fixture tests
- graph citation validation tests
- strategy comparison smoke
- redaction tests

Graph evaluation should remain CI-friendly by default. Heavy models, external APIs, and large datasets stay optional.

## Observability / LangSmith Boundary

Future observability export may include aggregate graph metrics and safe traces only. It must not export raw document text, raw chunk text, prompt material, full context, PII, credential values, or secret values.

## Reporting

Graph evaluation reports should include:

- dataset name
- strategy names
- metric summaries
- failure categories
- latency summaries
- context budget summaries
- graph path citation coverage

They should not include raw evidence payloads.
