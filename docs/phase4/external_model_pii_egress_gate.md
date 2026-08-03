# External Model PII Egress Gate v1

## Purpose

This gate minimizes personal and sensitive data sent to external model providers. It protects
authenticated user questions, retrieved context, source labels, system/task instructions,
structured response metadata, Agentic planner payloads, embeddings, and reranker inputs at the
last application boundary before a provider transport call.

The gate is independent from the RAG accuracy profile. Local LM Studio, Ollama, local
embedding/reranking, deterministic CI providers, Gold v2, Qdrant, and PostgreSQL are unchanged.

## Decision record

RAG-61 implements the following decisions:

1. External egress is denied by default. A configured external generation provider is not enough
   to authorize data transmission.
2. An explicit provider allowlist and `mask` policy are both required for normal external use.
3. PII detection and replacement execute locally without a remote detector or a new production
   dependency.
4. The same value receives the same typed placeholder within one outbound payload, preserving
   local referential utility without keeping a reversible mapping.
5. Secrets and content that cannot be safely masked are blocked instead of sent.
6. Audit records contain provider, purpose, action, PII types/counts, and reason codes only. They
   do not contain request text, detected values, placeholders-to-values mappings, or provider
   response bodies.
7. Raw egress is retained only as an explicit emergency rollback setting. It is never the default.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `EXTERNAL_MODEL_EGRESS_POLICY` | `deny` | `deny`, `mask`, or explicit raw `allow` |
| `EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS` | empty | Comma list or JSON list of approved providers |
| `PII_MASKING_ENABLED` | `true` | Must remain true when policy is `mask` |

Recognized external providers are `openai`, `anthropic`, `gemini`, `nvidia`, `bedrock`, and the
reserved future `qwen` provider. Recognized local providers are `fake`, `local`, `lmstudio`, and
`ollama`. An unclassified provider is blocked rather than treated as local.

### Safe promotion

Use an explicit allowlist with masking:

```text
EXTERNAL_MODEL_EGRESS_POLICY=mask
EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS=openai,bedrock
PII_MASKING_ENABLED=true
```

Keep the allowlist limited to providers that are approved for the deployment. A provider omitted
from the list is blocked even if API credentials and its generation setting are present.

### Immediate stop and recovery

To stop all external model transmission while preserving local RAG operation:

```text
EXTERNAL_MODEL_EGRESS_POLICY=deny
EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS=
```

Restart the backend with those settings. No database, Qdrant collection, Docker volume, or model
cache reset is required.

### Explicit raw rollback

The previous unmasked provider behavior can be restored only with both settings:

```text
EXTERNAL_MODEL_EGRESS_POLICY=allow
EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS=openai
```

`allow` is a high-risk diagnostic rollback. It can transmit raw user and document content and
must not be the standing internet-facing configuration. Restore `deny` or `mask` immediately after
the diagnostic window. The provider API key by itself never enables this rollback.

## Protected paths

| Path | External provider | Purpose label | Failure behavior |
| --- | --- | --- | --- |
| Answer generation, Graph extraction, auxiliary Judge | OpenAI, Anthropic, Gemini, NVIDIA, Bedrock | `generation` | `model_egress_blocked` |
| Agentic strategy planner | OpenAI | `agentic_strategy_planner` | planner fallback with safe reason |
| LLM tool-call planner, including retrieved snippets | OpenAI | `llm_tool_planner` | deterministic bounded fallback with safe reason |
| Embedding | Bedrock Titan | `embedding` | `model_egress_blocked` |
| Reranking | Bedrock | `rerank` | `model_egress_blocked` |

Production code constructs these adapters through their existing factories. Direct constructor
uses are limited to unit tests; the production tree has no alternate construction path.

Qdrant HTTP search sends vectors and filters to the configured Qdrant service, not to a model
provider, and is outside this model egress gate. The LM Studio readiness smoke uses fixed benign
text and remains local.

## Detection and transformation

The deterministic v1 detector covers:

- email addresses
- international and Japanese-style phone numbers
- labeled person names in English and Japanese
- labeled and recognizable Japanese/English street addresses
- Japanese postal codes
- labeled employee/customer/government identifiers
- IPv4 addresses
- payment-card-shaped values that pass a Luhn check

Detected spans are replaced with typed placeholders such as `[PII_EMAIL_1]`. Replacement is
performed across all string values of one structured payload so repeated values keep the same
placeholder. There is no deanonymization step and the mapping is discarded with the request.

The following are blocked rather than masked:

- private-key material
- Bearer tokens
- credential assignments such as API keys, access tokens, passwords, and secrets
- AWS access-key-shaped values
- long high-entropy blobs
- JWT and common external-provider token shapes
- reserved `[PII_*_N]` placeholder collisions
- payloads above the bounded local scan limits
- unsupported control characters
- unknown providers, missing allowlist entries, disabled masking, or policy `deny`

## Audit contract

The structured log event name is `model egress policy decision`. Its allowed fields are:

- `model_egress_provider`
- `model_egress_purpose`
- `model_egress_policy`
- `model_egress_action`
- `model_egress_entity_types`
- `model_egress_entity_counts`
- `model_egress_masked_count`
- `model_egress_reason_codes`

Do not add raw input, raw output, source labels, detected values, exception messages, request
bodies, headers, credentials, or reversible mappings to this event. Evaluation artifacts may
store aggregate counts and case hashes only.

## Automated gate

The synthetic-only fixture is
`backend/tests/fixtures/pii_egress_dev_v1.json`. It is separate from accuracy datasets and must not
be merged with Gold v2 scores or RAG-31 prompt-injection results.

Run the focused gate:

```bash
cd backend
pytest -q tests/test_model_egress.py
```

The tests prove:

- masked synthetic values have zero occurrences in outbound captured payloads
- clean Japanese/English input is exactly unchanged
- repeated PII keeps one placeholder within a payload
- deny, allowlist, disabled masking, unknown provider, and unmaskable data fail closed
- raw synthetic PII is absent from structured audit records
- external transports are not invoked on a block
- local LM Studio generation remains unwrapped
- Bedrock embedding/reranking are protected before their clients

No test sends fixture values to a live external provider. Do not publish fixture request text in CI
artifacts; publish only test status, counts, hashes, and safe reason codes.

## Known limits and next hardening step

Rule-based PII recognition cannot guarantee detection of every contextual name, address, locale,
free-form identifier, OCR error, obfuscation, or encoded value. This gate therefore complements,
rather than replaces, data classification, tenant access control, provider retention controls,
encryption, and incident monitoring.

The next safe extension is a locally hosted NER detector in shadow mode. Compare its findings with
the deterministic gate on synthetic tune/confirm sets, record type/count disagreements only, and
promote it only when recall improves without sending raw data or creating an unacceptable clean
text false-positive rate. The deterministic blocker remains the fallback if the NER component is
unavailable.

## Primary references

- OWASP GenAI Security Project,
  [LLM02:2025 Sensitive Information Disclosure](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)
- NIST, [Privacy Framework](https://www.nist.gov/privacy-framework)
- NIST, [De-Identification of Personal Information](https://www.nist.gov/publications/de-identification-personal-information)
- Microsoft Presidio,
  [Text anonymization](https://microsoft.github.io/presidio/text_anonymization/) and
  [Anonymizer operators](https://microsoft.github.io/presidio/anonymizer/)
