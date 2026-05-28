# PR-35 Advanced Import: HTML / XML / URL

## Purpose

PR-35 adds deterministic text ingestion for HTML/XML files and single URL ingest:

- `.html`
- `.htm`
- `.xml`
- `POST /api/v1/documents/url`

The implementation is limited to safe text extraction and structural metadata.
It does not add crawling, recursive web ingest, sitemap ingest, authenticated URL
fetch, cookies, JavaScript rendering, headless browser execution, OCR, image
upload, multimodal retrieval, Graph-RAG, AWS, S3, or OIDC/OAuth.

## Upload Validation

Allowed new upload extensions:

- `.html`
- `.htm`
- `.xml`

Allowed MIME types are limited to HTML/XML content types such as `text/html`,
`application/xhtml+xml`, `text/xml`, `application/xml`, `application/rss+xml`,
and `application/atom+xml`. SVG is rejected even though it is XML-shaped because
it can carry active script and image semantics outside PR-35 scope.

Validation rejects NUL bytes, DTD/entity declarations in XML, SVG payloads,
path traversal file names, executable suffixes, and unsupported binary content.

## HTML Extraction

HTML extraction uses a deterministic parser and stores only text and safe
structural metadata. It removes or ignores:

- `script`
- `style`
- `noscript`
- `iframe`
- `object`
- `embed`
- comments

Extracted metadata includes:

- `structure_type=html_section`
- `html_title`
- `heading_path`
- `element_type`
- `element_index`
- `source_type`
- `source_url` when the source is URL ingest

Links are treated as visible text only. `href` values are not stored.

## XML Extraction

XML extraction rejects DTD/entity declarations before parsing to avoid XXE and
entity-expansion behavior. It extracts leaf or own text with safe element path
metadata.

Extracted metadata includes:

- `structure_type=xml_element`
- `xml_root`
- `xml_path`
- `element_name`
- `element_index`
- `source_type`
- `source_url` when the source is URL ingest

Attributes are not persisted in PR-35. Raw XML is not logged, returned, or stored
in trace metadata.

## URL Ingest

`POST /api/v1/documents/url` is admin-only and CSRF-protected.

Request:

```json
{
  "url": "https://example.com/page.html",
  "title": "Optional Title"
}
```

The service fetches one URL, validates the response, stores the fetched bytes as
a document version, records safe URL metadata, and creates the normal
`document_ingest` job. The response does not include the fetched body.

Version metadata allowlist:

- `source_type=url`
- `source_url`
- `final_url`
- `fetched_at`
- `content_type`
- `redirect_count`

URL query strings and fragments are stripped from stored/displayed source URLs.

## Search And Citations

HTML/XML/URL chunks are stored as normal `document_chunks` rows with
`modality=text`. Existing dense, sparse, hybrid, and agentic retrieval paths can
search them after approval.

Source labels are enriched from safe metadata:

- `page.html / Heading: Product / API`
- `feed.xml / XML: feed / entry / title`
- `example.com/docs / Heading: Guide`

Raw HTML, raw XML, full fetched body, and raw chunk text are not written to logs,
trace JSON, score breakdowns, or normal API responses.

## CI And Tests

CI does not depend on real external internet access. URL fetch behavior is tested
with `httpx.MockTransport` and local fixtures. SSRF checks are tested with
deterministic resolver stubs.

## Known Limitations

- No crawler or recursive fetch.
- No authenticated URL fetch.
- No cookie/session/header customization.
- No JavaScript rendering.
- No PDF URL auto-import.
- No SVG support.
- DNS rebinding protection is limited to pre-request and redirect-time DNS/IP
  validation in PR-35; connect-level IP pinning is a future hardening item.
