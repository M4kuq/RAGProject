# External model egress governance

## Scope and decision

RAG-71 adds a fail-closed authorization layer after the local PII gate and immediately before
every supported external model transport. It is independent from retrieval/generation accuracy,
Gold v2, and the prompt-injection promotion profile.

An external request is sent only when all of the following are true:

1. `EXTERNAL_MODEL_EGRESS_POLICY=mask` and the provider is explicitly allowed.
2. An exact operator rule matches provider, model, and code-controlled purpose.
3. Every code-controlled data class is permitted by that rule.
4. The configured provider region is known and allowed.
5. The declared provider retention is within the deployment maximum and training is disabled.
6. Interactive purposes have an authenticated server request and explicit structured user consent.
7. Local masking succeeds and the remaining category combination has no unsupported
   re-identification risk.

Question, context, tool result, document content, or model output cannot set provider approval,
purpose, data class, region, retention, training, authentication, or consent. Those facts come
from application code, validated settings, and a request-local server context.

## Configuration contract

The safe default remains no external model transmission:

```env
EXTERNAL_MODEL_EGRESS_POLICY=deny
EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS=[]
EXTERNAL_MODEL_EGRESS_GOVERNANCE_ENABLED=true
EXTERNAL_MODEL_EGRESS_RULES=[]
```

To approve an external route, set `mask`, the provider allowlist, the provider region, the maximum
acceptable retention, and one exact rule per provider/model/purpose. The JSON below is a shape
example only; operators must verify the current provider contract and deployment region before
using it.

```env
EXTERNAL_MODEL_EGRESS_POLICY=mask
EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS=["openai"]
EXTERNAL_MODEL_EGRESS_PROVIDER_REGIONS={"openai":"us"}
EXTERNAL_MODEL_EGRESS_MAX_RETENTION_DAYS=0
EXTERNAL_MODEL_EGRESS_RULES=[{"provider":"openai","model":"approved-model","purpose":"generation","allowed_data_classes":["masked_personal_data","retrieved_context","system_instruction","user_question"],"allowed_regions":["us"],"retention_days":0,"training_allowed":false,"user_consent_required":true}]
```

The rule is an operator attestation of the approved provider contract. The application cannot
discover provider-side retention, training, or regional processing from a response, so unknown or
unverified facts must not be represented by an allow rule.

| Setting | Default | Enforcement |
| --- | --- | --- |
| `EXTERNAL_MODEL_EGRESS_GOVERNANCE_ENABLED` | `true` | May be disabled only in local/CI/test |
| `EXTERNAL_MODEL_EGRESS_GOVERNANCE_POLICY_VERSION` | `egress-v1` | Safe label included in audit |
| `EXTERNAL_MODEL_EGRESS_RULES` | `[]` | Exact provider/model/purpose contracts |
| `EXTERNAL_MODEL_EGRESS_PROVIDER_REGIONS` | `{}` | Unknown region blocks; Bedrock defaults to `AWS_REGION` |
| `EXTERNAL_MODEL_EGRESS_MAX_RETENTION_DAYS` | `0` | Rules above the maximum block |
| `EXTERNAL_MODEL_EGRESS_LOCAL_FALLBACK_ENABLED` | `false` | Optional generation-only local fallback |
| `EXTERNAL_MODEL_EGRESS_LOCAL_FALLBACK_PROVIDER` | `lmstudio` | `lmstudio`, `ollama`, or test-only `fake` |
| `EXTERNAL_MODEL_EGRESS_LOCAL_FALLBACK_MODEL` | empty | Required when fallback is enabled |

`training_allowed=true` always blocks. `EXTERNAL_MODEL_EGRESS_POLICY=allow` is also blocked while
governance is enabled, because an approved governance route must pass local masking.

## Purpose and data-class inventory

| Code-controlled purpose | Possible data classes | Consent |
| --- | --- | --- |
| `generation` | `user_question`, `retrieved_context`, `system_instruction`, optional task/schema, masked personal data | required |
| `agentic_strategy_planner` | `user_question`, `retrieval_metadata`, `system_instruction` | required |
| `llm_tool_planner` | `user_question`, `tool_result`, `retrieval_metadata`, `system_instruction` | required |
| `rerank` | `user_question`, `retrieved_context` | required |
| `embedding_query` | `user_question` | required |
| `embedding_document` | `document_content` | rule-controlled |
| `graph_extraction` | `document_content`, `system_instruction`, task/schema | rule-controlled |
| `evaluation_generation` | question/context/instruction classes | rule-controlled |
| `evaluation_judge` | question/context/instruction/task/schema classes | rule-controlled |
| `security_evaluation` | question/context/instruction classes | rule-controlled |

The authenticated RAG endpoints accept `external_model_egress_consent`, defaulting to `false`.
The boolean is never appended to a prompt. Consent is request-local and is cleared even when the
pipeline raises. Replays that return stored results do not create a new external transfer.

## Re-identification and failure behavior

After local masking, the guard adds `masked_personal_data` to the aggregate classification. It
blocks government/payment identifiers, name-plus-address combinations, and payloads containing
three or more detected personal-data categories. Unsupported secrets, credentials, placeholder
collisions, control characters, and oversized payloads continue to fail closed in the PII gate.

Stable reason codes distinguish missing rules, unknown/not-allowed region, incompatible retention
or training, missing authentication/consent, disallowed data classes, and post-mask
re-identification risk. No reason code contains request content.

When explicitly configured, answer generation falls back to a local provider only for
`model_egress_blocked`. Timeout, authentication, HTTP, invalid-response, and other provider errors
remain visible and do not silently switch models. The response metadata records the effective
local provider/model so cost and provenance are not attributed to the blocked external selection.
Agentic planners retain their existing deterministic fallback.

## Audit contract

The `model egress policy decision` event contains only controlled labels and aggregates:

- provider, purpose, policy, action, and policy version
- sorted data classes
- detected entity types and counts
- masked count and stable reason codes

It must never contain question/context/chunk/answer text, detected values, canaries, PII, secrets,
credentials, headers, provider response bodies, or reversible mappings. CI, PR, and Jira evidence
must likewise contain only test counts, hashes, and stable reason codes.

## Stop, rollback, and recovery

Immediate external stop requires only configuration and restart:

```env
EXTERNAL_MODEL_EGRESS_POLICY=deny
EXTERNAL_MODEL_EGRESS_ALLOWED_PROVIDERS=[]
EXTERNAL_MODEL_EGRESS_RULES=[]
EXTERNAL_MODEL_EGRESS_LOCAL_FALLBACK_ENABLED=false
```

No database, Qdrant, Neo4j, Docker volume, model cache, or accuracy profile reset is required.
Disabling governance or using raw `allow` is local/CI/test diagnostic behavior only; production
settings reject disabled governance, and an enabled guard rejects unmasked external egress.

## Verification

The focused synthetic gate is:

```bash
cd backend
pytest -q tests/test_model_egress.py tests/test_external_egress_governance.py
```

It verifies consent bypass zero across content claims, exact provider/model/purpose matching,
region/retention/training fail-closed decisions, post-mask re-identification blocks, unchanged
clean payloads, raw-free audit fields, pre-transport blocks, local fallback provenance, and that
non-policy transport failures are not hidden. It performs no live external provider call and is
kept separate from normal RAG accuracy evaluation.

### 2026-08-05 local evidence

- case-set fingerprint: `d03e61b1125fa8bdb694d5bcdcb857bcb4726a4649f10754f8ece7d602adea24`
- 26 governed scenarios; consent bypass `0/10`
- incompatible exact-policy/region/retention/training/raw-rollback bypass `0/8`
- post-mask re-identification bypass `0/2`
- captured governed transports `3`; synthetic raw-value leakage `0`
- clean payload mutation `0`
- backend full suite: `1101 passed`, `21 skipped`; no failures
- frontend full suite: `107 passed`; lint, typecheck, and production build passed
- normal and CI Compose configuration validation passed

The counts above are security-gate evidence, not normal RAG accuracy, citation, or Gold v2
metrics. The scan compared runtime-generated synthetic exact values with added PR lines and found
zero exact matches; no raw cases are stored in this result section.
