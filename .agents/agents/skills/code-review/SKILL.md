---

name: code-review

description: Use when reviewing code changes, diffs, pull requests, or uncommitted work for correctness, regressions, maintainability, risky behavior, and missing tests.

---



# Code Review Skill



Use this skill when asked to review code, diffs, pull requests, commits, or uncommitted changes.



## Review priorities



Review in this order:



1. Correctness

2. Bugs and edge cases

3. Security and unsafe behavior

4. Regression risk

5. Test coverage

6. Maintainability

7. Performance, only when relevant



## Output format



Group findings as:



- Must Fix

- Should Fix

- Nice to Have



For each finding, include:



- Location

- Problem

- Impact

- Suggested fix



## Rules



- Do not nitpick style unless it affects readability, maintainability, or consistency.

- Do not invent requirements.

- Do not request large rewrites unless the current approach is fundamentally unsafe or broken.

- Prefer concrete examples or patch suggestions.

- If the code is acceptable, say so clearly.

- If there are no Must Fix issues, explicitly state that.



## Review checklist



Check for:



- Incorrect assumptions

- Missing validation

- Null/None/undefined handling

- Off-by-one or boundary errors

- Race conditions

- Transaction or rollback issues

- Unhandled exceptions

- Inconsistent error responses

- Missing tests for changed behavior

- Unnecessary dependency additions

- Secret or sensitive data exposure

