---
name: code-review
description: Use when reviewing code changes, diffs, pull requests, commits, or uncommitted work. Follow the repository multi-pass protocol from docs/code_review.md, including P0-P3 severity, finding quality bar, redaction rules, and review/fix/re-review loops until actionable findings are zero when remediation is requested.
---

# Code Review Skill

Use this skill to review a diff, PR number, branch, commit range, or uncommitted work for actionable issues introduced by the current change.

This repository's source of truth is `docs/code_review.md`. If that file exists in the target checkout, read it before reviewing and treat it as authoritative. This skill restates the protocol so it can be executed directly.

## Inputs

Accept one or more of:

- PR number and repository, for example `PR #77`.
- Branch or commit range, for example `origin/main...feature/eval-model-comparison`.
- Local diff, patch, or uncommitted worktree.
- Optional PR description, commit messages, CI results, existing review comments, and user-specified risk areas.

When a PR or branch is provided, reconstruct the review target with the exact requested base and head. Do not silently review a different range.

## Redaction Rules

- Do not output secrets, API keys, tokens, passwords, cookies, private keys, session IDs, `.env` values, or credentials.
- Do not output raw prompts, raw answers, raw retrieved context, raw chunks, or private/PII text from datasets, traces, logs, or evaluation records.
- When sensitive values are relevant to a finding, identify the field or code path and redact the value, for example `[REDACTED_TOKEN]`.
- Do not paste large private repository content into the final review. Cite file paths and line numbers instead.

## Mandatory Multi-Pass Protocol

Do not perform a single-pass review. Execute Pass 0 through Pass 9 before the final answer.

### Pass 0: Scope and intent reconstruction

Reconstruct the intent from the diff, PR description, commit messages, tests, names, and surrounding code.

Identify changed files, public interfaces, API contracts, data models or schemas, authorization or trust boundaries, side effects, async/queue/cron/retry/webhook/background behavior, configuration or deployment assumptions, and tests.

If intent remains unclear, continue and record assumptions under `Open questions / assumptions`.

### Pass 1: Diff coverage

Review every changed file. For each one, inspect enough surrounding code to understand callers, callees, data flow, control flow, error handling, invariants, existing tests, and adjacent similar implementations.

Do not judge a changed line in isolation when surrounding code determines behavior.

### Pass 2: Correctness and regression review

Look for mismatches with apparent intent, broken edge cases, null/undefined/empty/zero/negative/NaN/timezone/locale/encoding/boundary bugs, incorrect branching/defaults/error handling, swallowed errors, partial failures, changed return values/status codes/exceptions/response shapes, compatibility breaks, invalid state transitions, and bad ordering/uniqueness/presence assumptions.

### Pass 3: Security and trust-boundary review

Look for authentication or authorization bypass, privilege escalation, tenant/workspace/account isolation violations, unsafe user-controlled input, SQL/NoSQL/shell/template/path/LDAP/log injection, XSS, CSRF, SSRF, open redirect, insecure deserialization, request smuggling, missing webhook signature verification, sensitive data in logs/errors/analytics/traces/responses, unsafe CORS/cookie/cache/header/redirect behavior, and prompt/tool injection or LLM data exfiltration.

Treat concrete auth, tenant-boundary, data-exposure, or injection issues as P1 or higher unless clearly low impact.

### Pass 4: Data, database, and migration review

When persistence, schemas, migrations, ORM models, serialization, or data contracts changed, look for data loss, irreversible migrations, unsafe defaults, missing backfill, nullable/non-nullable incompatibility, uniqueness or foreign-key violations on existing data, table locks/rewrites/long migrations, unsafe deployment order, rollback incompatibility, old/new app read-write incompatibility, inconsistent serialization, and cache key/invalidation bugs.

For zero-downtime systems, explicitly consider expand/migrate/contract safety.

### Pass 5: Concurrency, idempotency, and distributed-systems review

Look for race conditions, non-atomic read-modify-write sequences, missing transactions or locks, duplicate side effects, non-idempotent retries, unsafe webhook replay behavior, queue redelivery bugs, cron overlap, lost updates, stale cache reads, eventual consistency assumptions, cross-service ordering assumptions, and timeout/cancellation/retry interactions.

Payment, billing, inventory, quota, permission, notification, and provisioning paths require extra scrutiny.

### Pass 6: Performance and scalability review

Look for N+1 queries, unbounded loops/queries/memory/payload/recursion, unnecessary synchronous hot-path work, repeated expensive computation, missing pagination/filtering/batching/streaming, inefficient indexes or query shapes, increased lock contention, accidental fan-out, and realistic performance regressions.

Only flag performance issues with a plausible scale or hot-path scenario.

### Pass 7: Test and verification review

Evaluate whether changed behavior is adequately covered. Look for missing tests for primary behavior, edge cases, failure paths, authorization/tenant boundaries, migrations and existing data, concurrency/retries/idempotency, API compatibility, serialization/deserialization, feature flags/config variants, and regression cases implied by the change.

A missing test is a finding only when there is a concrete risk that the test would catch.

### Pass 8: False-positive and duplicate filtering

Before finalizing findings, re-read each candidate against the actual code. Verify it is introduced or materially worsened by the current diff, has a concrete failure mode, cites an accurate file and line, has justified severity, and is not a duplicate.

Downgrade uncertain findings into `Open questions / assumptions`. Remove style preferences, broad refactors, speculative concerns, and invented requirements.

### Pass 9: Final coverage check

Check whether any high-risk area was touched but not reviewed: authentication, authorization, tenant isolation, payments, billing, webhooks, background jobs, migrations, data deletion, PII, secrets, public API contracts, caching, concurrency, retry behavior, LLM tool use, or security-sensitive configuration.

If a high-risk area was touched, make sure at least one pass explicitly considered it.

## Severity

Use P0, P1, P2, and P3.

- P0: Release-blocking severe production impact, such as data loss, severe security vulnerability, outage, broadly broken auth/authz, irreversible migration failure, or widespread customer-visible breakage.
- P1: Serious issue that should be fixed before merge, such as important-path business logic bug, authz bypass, tenant isolation bug, API contract breakage, dangerous migration, duplicate money movement, sensitive data leak, concrete injection, or high-risk missing test for security/billing/migration/data integrity.
- P2: Meaningful issue that should be addressed soon, such as edge-case correctness bug, realistic performance regression, incomplete error handling, non-critical API inconsistency, maintainability risk likely to cause bugs, or missing test for changed non-critical behavior.
- P3: Minor but useful issue, such as localized cleanup, confusing naming, low-risk test improvement, or small documentation mismatch. Avoid P3 unless clearly useful.

## Finding Quality Bar

A finding is valid only if all are true:

1. It is introduced or materially worsened by the current diff.
2. It is actionable by the author.
3. It has a concrete failure mode, exploit path, regression, or maintainability consequence.
4. It cites the most relevant file and line.
5. It explains when the issue occurs.
6. It has appropriate severity.
7. It is not a duplicate.

When in doubt, do not inflate severity. When evidence is incomplete, use `Open questions / assumptions`.

## Output Format

Start with findings.

If there are findings, use exactly:

```text
Findings

[P1] Short imperative title

File: path/to/file.ext:line
Issue:
Why it matters:
When it happens:
Suggested fix:
Confidence: high / medium / low
```

Then include:

```text
Open questions / assumptions
- ...

Recommended tests
- ...

Coverage notes
- Changed files reviewed:
- High-risk areas considered:
- Areas not fully verified:

Overall correctness
- patch is correct / patch is incorrect / uncertain
- Concise justification:
```

If there are no findings, say:

```text
No findings.

Open questions / assumptions
- ...

Recommended tests
- ...

Coverage notes
- Changed files reviewed:
- High-risk areas considered:
- Areas not fully verified:

Overall correctness
- patch is correct / uncertain
- Concise justification:
```

## Line References

Cite the smallest useful line range. Point to the changed line or the closest line that makes the issue actionable. For cross-file interactions, cite the primary changed line first and mention supporting files in the explanation. Do not cite a line unless the line number was verified.

## Review Style

Be direct, specific, and concise. Do not praise the patch. Do not summarize the whole diff unless needed for a finding. Do not provide generic best practices. Do not propose large rewrites unless the current design creates a concrete issue. Prefer practical fixes.

## Review and Fix Loop

When the user asks for remediation until clean:

1. Run the full multi-pass review and record actionable findings.
2. Fix only actionable findings in the requested target branch/worktree.
3. Use explicit file paths when staging changes; do not stage unrelated work.
4. Run the smallest relevant checks first, then broader checks required by the user or repo.
5. Re-run the full multi-pass review on the updated diff.
6. Repeat until actionable findings are zero, or stop only when blocked by an external condition and report the remaining findings, blocker, and head SHA.

If GitHub review threads correspond to fixed issues and the user asked to resolve them, resolve only those verified as addressed.

## Ignore

Do not flag pre-existing issues not made worse by this diff, subjective style preferences, broad refactors, speculative risks without concrete failure mode, unchanged-code comments unless the diff depends on them, missing tests without a specific changed behavior/failure scenario, or formatting issues handled by automated tooling.
