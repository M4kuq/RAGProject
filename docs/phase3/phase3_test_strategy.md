# Phase3 Test Strategy

PR-45 defines the test strategy. It does not add tests because it does not change runtime behavior.

## Graph Schema Tests

- migration applies and rolls back in isolated test DB
- FK constraints for chunks, versions, and retrieval runs
- unique/idempotency constraints for entities, mentions, relations
- stale version filtering
- JSON safe schema validation

## Entity Extraction Unit Tests

- deterministic fake extractor output
- rule-based extractor fixtures
- normalization and alias merging
- confidence boundaries
- redaction of unsafe fields
- failure error codes

## Relation Extraction Unit Tests

- relation type normalization
- source chunk support required
- `evidence_text_hash` created without storing evidence text
- relation confidence filtering
- duplicate relation idempotency
- hallucination guard tests

## Graph Index Job Tests

- queued/running/succeeded/failed lifecycle
- retry/reclaim behavior
- no external I/O inside DB transaction
- version update/reindex behavior
- partial failure recovery

## Graph Retrieval Tests

- entity lookup
- relation traversal
- multi-hop path search
- neighborhood expansion bound
- graph score breakdown
- fallback hybrid/dense behavior
- no-context behavior

## Graph Citation Tests

- node to source chunk mapping
- edge to source chunk mapping
- path to citations
- stale version handling
- retrieval run item constraint
- source locator integration
- raw evidence absence

## Graph Router Tests

- multi-hop detection
- relation query detection
- entity comparison detection
- graph disabled fallback
- traversal budget fallback
- Auto graph tool future gating
- safe trace fields

## Graph Evaluation Tests

- entity precision/recall fixtures
- relation accuracy fixtures
- path relevance fixtures
- graph citation coverage
- dense vs hybrid vs graph comparison smoke

## OCR Tests

- scanned PDF fixture parsing
- OCR confidence thresholds
- region source locator mapping
- OCR redaction tests
- optional heavy model checks outside normal CI

## Multimodal Tests

- image upload validation
- image metadata redaction
- multimodal citation region mapping
- viewer/admin boundary tests

## Security / Redaction Tests

- no unsafe keys in graph traces
- no raw document text in graph tables
- no raw chunk text in graph debug
- no full context in artifacts
- no credential or secret values in logs
- MCP safe output tests

## Regression Tests

- dense retrieval unchanged
- hybrid retrieval unchanged
- agentic router unchanged
- Auto / `llm_tool_orchestrator` unchanged unless graph explicitly enabled
- Context Budget / Evidence Pack / Tool Result Compression still applied

## Performance Smoke

- graph traversal stays within hop/time/path budgets
- graph + vector hybrid latency summary recorded
- graph explosion guard drops paths deterministically

## CI Strategy

Default CI should use deterministic fake extractors and small fixtures. Heavy OCR, external providers, model downloads, AWS, and online evaluation should be opt-in jobs, not required PR gates until separately accepted.
