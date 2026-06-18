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

## PR-53 Implementation Scope

PR-53 extends the existing evaluation runner instead of adding a separate graph-only
framework.

Implemented comparison targets:

- `dense`
- `hybrid`
- `agentic_router`
- `graph_postgres`
- `graph_neo4j`

Implemented cache modes:

- `default`
- `disabled`
- `cold`
- `warm`

The runner stores each comparison target as safe metadata:

- comparison label
- retrieval strategy
- graph store provider
- cache mode

Graph provider differences stay in the evaluation target metadata and report
summary. `graph_neo4j` is optional; if Neo4j is not configured or available,
that target records a safe not-applicable reason while the overall evaluation
continues.

## PR-53 Metrics

PR-53 adds these safe metrics on top of the existing evaluation metrics:

- `graph_path_relevance`
- `graph_citation_coverage`
- `multi_hop_answerability`
- `cache_hit_rate`
- `cache_saved_latency`
- `entity_relation_quality_summary`

Graph path relevance uses safe expected entity labels, relation types, and hop
counts from case metadata. Entity/relation quality is reported as counts only.
Cache saved latency compares warm-cache samples with the matching cold-cache
baseline for the same case and strategy/provider label.

## PR-53 Safe Reporting

Evaluation result details and admin summaries must not include:

- raw query text
- raw prompts
- raw chunk text
- full context
- raw graph evidence
- PII
- credential or secret values

Allowed values include metric scores, safe labels, relation types, source chunk
IDs, retrieval run IDs, providers, cache status, latencies, counts, hashes, and
not-applicable reason codes.

## PR-53 Dataset

`phase3_graph_multi_hop` is a small synthetic fixture for multi-hop GraphRAG
evaluation. It intentionally uses non-private demo concepts and safe metadata
only.

## PR-53 Failure Promotion

The existing failure promotion path remains the mechanism for turning low
quality evaluation items into reusable failure datasets. PR-53 adds safe target
metadata to failure snapshots so promoted failures can distinguish strategy,
provider, and cache mode without copying raw question, prompt, context, or
evidence text.

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
