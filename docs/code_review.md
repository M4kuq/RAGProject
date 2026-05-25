# Code Review Guidelines

## Review objective

Find actionable issues introduced by the current change.

Prioritize:
- correctness
- security
- performance
- maintainability
- test coverage
- backward compatibility
- operational risk

## Non-goals

Do not flag:
- pre-existing issues
- broad refactoring ideas
- subjective style preferences
- speculative concerns without a concrete failure mode
- unrelated architecture opinions

## Severity

Use P0, P1, P2, and P3.

P0:
- release-blocking
- data loss
- severe security vulnerability
- production outage

P1:
- must fix before merge
- serious correctness issue
- authorization or tenant isolation issue
- API contract breakage
- dangerous migration
- high-risk missing test

P2:
- should fix
- edge-case bug
- meaningful performance regression
- maintainability risk
- missing test for changed behavior

P3:
- minor but useful
- low-risk cleanup
- small documentation or naming issue

Avoid P3 unless clearly valuable.

## Output format
Please translate all output into Japanese.
Start with findings.

For each finding:

```text
[P1] Short title

File: path/to/file.ext:line
Issue:
Why it matters:
When it happens:
Suggested fix:
