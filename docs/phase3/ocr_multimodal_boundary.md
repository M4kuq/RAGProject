# OCR / Multimodal Boundary

PR-45 does not implement OCR or multimodal features. It defines how they should join Phase3 after the Graph-RAG baseline is stable.

## OCR Timing

OCR belongs to Phase3 middle milestones, starting around PR-51. Graph-RAG text path should land first so citation and source locator patterns are already stable.

## PaddleOCR Direction

PaddleOCR remains the candidate OCR engine for scanned PDFs and image inputs. Future work should define:

- installation and model download policy
- local deterministic test fixtures
- confidence thresholds
- language configuration
- CPU/GPU behavior
- optional heavy checks outside normal CI

## Inputs

Candidate inputs:

- scanned PDF
- PNG/JPEG images
- image pages embedded in supported documents

Input validation should remain strict and admin-reviewed.

## OCR Region Metadata

OCR should create bounded region metadata, not raw OCR dumps:

- page number
- bounding box
- OCR confidence
- text hash
- source locator refs
- document version/chunk refs

## Image Source Locator

Image source locators should extend the existing source locator path:

```text
logical_document -> document_version -> image/page/region -> citation locator
```

## Multimodal Citation

Multimodal citations should map answers to:

- source chunk when text-backed
- OCR region when scanned text-backed
- image region when visual evidence-backed
- graph path refs when relation-backed

## Relationship To Graph

OCR text can produce entity mentions and relations after it is chunked and source-located. Graph extraction should treat OCR chunks as source chunks with extra region metadata.

## Raw OCR / Image PII Handling

- Raw OCR text should not appear in logs, traces, artifacts, debug panels, or MCP output.
- Image metadata should be bounded and stripped of sensitive values when possible.
- OCR/image evidence sent to external providers requires explicit opt-in policy.
- Viewer UI should show only bounded citation previews and regions.

## PR-51+ Handoff

Move to PR-51 or later:

- PaddleOCR setup
- scanned PDF ingest
- image upload lifecycle
- OCR region schema/migration
- OCR worker job
- multimodal citation panel
- OCR evaluation metrics
