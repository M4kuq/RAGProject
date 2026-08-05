# Qwen Flash → Plus cost and escalation controls

Status: Draft / disabled by default (`QWEN_CASCADE_ENABLED=false`)

This profile adds a server-controlled Qwen Flash → Plus answer-generation cascade without
weakening the existing public-request admission gate or external-model egress governance.
It does not enable a live provider call in CI, tests, or evaluation jobs.

## Control order

1. RAG-63 admits the request using the existing per-user/global request, concurrency, and
   work-unit budgets.
2. The cascade accepts only server-produced routing facts: canonical retrieval strategy,
   retrieval sufficiency, and a structured Flash transport result.
3. RAG-71 applies the exact provider/model/purpose rule, server-side consent, data classes,
   region, retention, training, PII masking, secret detection, and re-identification checks.
   A blocked request creates no cost reservation and never reaches transport.
4. PostgreSQL serializes reservation under a transaction-scoped advisory lock. The ledger
   reserves per-user/provider daily input tokens, output tokens, currency cost, escalation
   count, shared RPM, and shared concurrency before transport.
5. The provider call uses a server-owned model ID, `temperature=0`,
   `enable_thinking=false`, a bounded output token count, and a fixed timeout.
6. Provider `usage.prompt_tokens` and `usage.completion_tokens` atomically finalize cost at
   the price snapshot stored on the reservation. Missing usage fails closed and retains the
   conservative reservation.

The input reservation uses the UTF-8 byte length of the already RAG-71-protected request.
This deliberately over-reserves relative to ordinary tokenizers, so a local estimate cannot
understate the hard token/cost admission boundary.

## Trusted escalation policy

Flash is always attempted first. Plus is eligible only when retrieval is sufficient and one
of these server-side facts is true:

- the canonical strategy is GraphRAG or an Agentic strategy;
- Flash returned a structured invalid-response, missing-usage, or output-truncation reason.

Question text, retrieved evidence, and tool output are never inspected for escalation
instructions. Qwen is intentionally absent from the user-selectable model-key parser. The
ledger enforces one Plus reservation per request even when answer-generation retry runs.

If Plus admission or transport fails, the completed Flash result is returned. If Flash has
no safe completed result, the request abstains or returns a stable 429/503 reason with
`Retry-After`. Provider failures feed a database-backed circuit breaker shared by app
instances. Half-open state permits one leased probe.

## Ledger privacy and accounting

`qwen_cost_reservations` contains only:

- HMAC-SHA256 subject and request identifiers;
- provider, exact model ID, tier, call index, and stable reason code;
- reserved/actual input and output tokens;
- immutable pricing version, currency, unit prices, and reserved/actual cost;
- reservation/finalization timestamps and status.

It never stores prompt, retrieved context, document chunks, model output, PII, credentials,
or provider response bodies. Failed post-transport reservations retain the reserved amount
because billing is uncertain; pre-transport cancellations do not consume daily budget.

## Official Alibaba contract snapshot

Verified on 2026-08-05 against Alibaba Cloud primary documentation:

- [Model Studio endpoints](https://www.alibabacloud.com/help/en/model-studio/what-is-model-studio):
  the Japan OpenAI-compatible endpoint is workspace-specific and region-bound.
- [OpenAI-compatible Chat API](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions):
  the configured snapshot IDs are supported and `enable_thinking=false` disables thinking.
- [Text generation response](https://www.alibabacloud.com/help/en/model-studio/text-generation):
  accounting uses the documented prompt/completion/total usage fields and finish reason.
- [Model pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing): the
  `alibaba-2026-07-15-tokyo-tier-1` configuration snapshot uses the documented <=256K rates:
  Flash input/output USD 0.165/0.99 per million tokens and Plus input/output USD 0.4/1.6.
- [Rate limits](https://www.alibabacloud.com/help/en/model-studio/rate-limit) and
  [rate-limit practices](https://www.alibabacloud.com/help/en/model-studio/rate-limiting-best-practices):
  the app default is a lower shared 30 RPM boundary and also limits concurrency.
- [Error codes](https://www.alibabacloud.com/help/en/model-studio/error-code): 429 is mapped to
  a stable reason and a bounded `Retry-After` value.

Pricing and provider quotas can change. Rates are configuration, not compiled constants;
operators must create a new immutable pricing-version label whenever they update rates.
The configured context bound is below the first 256K price tier.

## Promotion and rollback

Promotion requires all of the following with mocked transport only:

- cost-attack and untrusted-routing attempts produce zero content-driven Plus calls;
- concurrent reservations cannot overspend the configured admission budget;
- one request produces at most one Plus reservation;
- provider rate, concurrency, timeout, 429, failure, and circuit paths have stable results;
- clean dense requests remain Flash-only and preserve citation validation;
- RAG-63 and RAG-71 regression suites remain green;
- no raw request/evidence/output or sensitive value appears in ledger, logs, artifacts,
  Jira, or the PR.

Rollback is configuration-only: keep or restore `GENERATION_PROVIDER=lmstudio` and
`QWEN_CASCADE_ENABLED=false`, then restart the API. Do not drop the ledger during rollback;
retention pruning removes terminal aggregate records after the configured interval. No
merge, deployment, live call, or secret entry is part of this Draft change.
