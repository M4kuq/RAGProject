# Phase3 Risk Register

| Risk | Impact | Mitigation | Owner/PR |
|---|---|---|---|
| Entity extraction quality | Low-quality entities reduce graph recall and confuse routing. | Start with deterministic fixtures, confidence thresholds, and conservative normalization. | PR-47 |
| Relation hallucination | Unsupported edges can create false multi-hop explanations. | Require source chunk support, relation confidence, evidence hash, and citation validation. | PR-47 / PR-49 |
| Graph explosion | Traversal can become slow and noisy. | Enforce max hops, max neighbors, max paths, and traversal time budget. | PR-48 |
| Stale graph after document version update | Old evidence may ground current answers. | Track document version refs and default to active-version filtering. | PR-46 / PR-48 |
| Citation mismatch | Graph path may not map to selected chunks. | Validate graph node/edge/path refs against retrieval run items before citation. | PR-49 |
| Graph path explainability | Users may not understand why a path matters. | Show safe path labels, relation labels, confidence, and source citations. | PR-49 / PR-50 |
| Latency increase | Graph + vector hybrid can slow answer time. | Add latency trace, traversal budgets, and fallback strategy. | PR-48 / PR-50 |
| Context budget pressure | Graph evidence can consume too much context. | Treat graph paths as Context Budget candidates and compress through Evidence Pack. | PR-49 |
| OCR accuracy | OCR errors can create bad entities and citations. | Delay OCR until graph citation is stable; add confidence and region tests. | PR-51 |
| Image/PII risk | Images and OCR can include sensitive information. | Add strict upload validation, redaction, and viewer/admin boundaries. | PR-51 / PR-52 |
| External API cost | Optional provider calls may become expensive. | Keep external calls opt-in with timeouts, budgets, and CI exclusion. | PR-54 |
| AWS cost | Cloud resources can create ongoing cost. | Use separate deploy/aws branch, cost controls, and no default provisioning. | PR-57 |
| OIDC complexity | External auth can break local demo or RBAC. | Preserve local auth and add OIDC as opt-in provider. | PR-56 |
| Migration complexity | Graph tables add FK/index complexity. | Stage schema in PR-46, use migrations with tests, avoid backfill in migration. | PR-46 |
| Debug data leakage | Graph debug can expose unsafe evidence. | Safe trace schemas, redaction tests, and admin-only panels. | PR-50 |
| Evaluation false confidence | Narrow fixtures may overstate graph quality. | Separate unit, smoke, and optional evaluation datasets with clear coverage. | PR-50 / PR-58 |
