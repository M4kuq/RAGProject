# Frontend security final integration

## Decision target

RAG-77 combines the immutable histories of PR #146, #147, and #148 into one
canonical Draft review target. It verifies dependency remediation, React Router/Auth
behavior, and repository-wide secret detection on the same integration head. Passing
this gate means **frontend security integration ready**; it does not mean production
secure or deployed.

## Source and rollback manifest

| Source | Head | Purpose |
| --- | --- | --- |
| PR #146 | `d3386e37f0e2849e7ab446b61e31f8794674b384` | Critical/High dependency remediation and same-origin redirect gate |
| PR #147 | `b957462237650330ea0ccc86dc1e469fd58ddf83` | Node 22.22+, React 19, Vite 7, React Router 8 |
| PR #148 | `49fd24c5bbb2b4b0a6e25ef87b821d4209b1871b` | Exact-fingerprint repository-wide secret scan |

The reviewable merge commit is
`18384b3d623a900e597b2b30305ff1e03e763453`. Its first parent is PR #147 and
its second parent is PR #148. PR #146 is an ancestor of PR #147. The source changed-file
union is 41, the PR #147/#148 intersection is zero, and integration missing files must
remain zero. The machine-readable authority is
`docs/security/frontend_security_integration_manifest.json`.

The rollback SHA is PR #147 head
`b957462237650330ea0ccc86dc1e469fd58ddf83`. While Draft, rollback is simply not
merging the canonical PR.

## Dependency audit regression policy

Raw `npm audit --json` stdout and stderr are captured only inside the Node process.
They are never printed, written to a file, uploaded as an artifact, or copied into Jira
or a PR. Output is restricted to aggregate severity counts, advisory ID, package,
dependency class, review deadline, and stable reason codes.

The gate runs both full and `--omit=dev` audits:

- production: Low, Moderate, High, and Critical must all be zero;
- full: Moderate, High, and Critical must be zero;
- any new Low fails;
- an unavailable registry or non-audit response is retried once, then fails with
  `audit_unavailable`;
- malformed JSON is retried once, then fails with `malformed_output`.

The only conditional tracked debt is:

| Advisory | Package | Class | Required state | Review deadline |
| --- | --- | --- | --- | --- |
| `GHSA-4x5r-pxfx-6jf8` | `@babel/core` | transitive dev-only | Low, `fixAvailable=false`, absent from the production audit | 2026-09-04 UTC |

This is not an open-ended risk acceptance. If the advisory disappears, the gate passes
cleanly with zero findings. If a fix becomes available, the path becomes production,
the ID/package/severity changes, another Low appears, or the deadline expires, CI fails.

### Runtime advisory drift and remediation

At implementation time the live audit correctly rejected the conditional-debt path with
`fix_available`. The GitHub Advisory entry now identifies `@babel/core` 7.29.6 as a
patched Babel 7 release, and Babel published that version as a security release:

- <https://github.com/advisories/GHSA-4x5r-pxfx-6jf8>
- <https://github.com/babel/babel/releases/tag/v7.29.6>

The integration therefore pins only the transitive `@babel/core` resolution to 7.29.6
through an npm override. A generic update candidate changed 21 packages and was rejected;
the exact override changed six Babel-internal lockfile entries. On a clean Node 22.22/npm
10 install, both production and full audits report Low/Moderate/High/Critical zero, and
`npm ls --all` reports zero dependency problems. The conditional rule remains tested as
a fail-closed policy contract, but no tracked debt is active on this integration head.

## Negative controls

The Node built-in test suite verifies:

1. production Low/Moderate/High/Critical all fail;
2. new dev Low/Moderate/High/Critical all fail;
3. only the exact Babel Low/no-fix/deadline-active tuple passes with tracked debt;
4. `fixAvailable=true`, expiry, package drift, advisory drift, severity drift, and
   dependency-class drift fail;
5. malformed and unavailable audit responses fail closed;
6. formatted output contains no dependency path or raw audit field.

## Combined verification

The pre-push combined gate on the integration tree produced the following raw-free
results. GitHub CI remains a separate required gate.

| Gate | Result |
| --- | --- |
| Clean Node/npm install | Node 22.22.3 / npm 10.9.8; `npm ci` pass |
| Dependency policy | 19/19 negative controls pass; production and full audit all severities 0; dependency-tree problems 0 |
| Route/auth regression | 4 files / 67 tests, 3/3 repeats pass |
| Full frontend regression | 17 files / 111 tests, 3/3 repeats pass |
| Frontend static/build | lint, typecheck, Vite production build pass; 197 modules transformed |
| Docker/Compose | Node 22.22 frontend test/build images and no-dependency Compose smoke pass; Compose config valid |
| Backend fixture regression | focused 33 tests pass; full 972 pass / 19 skip; Ruff format/check and mypy 272 files pass |
| Secret scan | current/history unignored 0; mutation controls 3/3; output redaction 100% |
| Source manifest | sources 3; union 41; intersection 0; missing 0; integration-only files 8 |

The host Docker daemon could not allocate another default-network subnet during the
first Compose run. The frontend-only smoke was repeated on an existing user-defined
Docker network without dependencies; no service or volume was created, reset, or
deleted. The source RAG-75 document remains an immutable snapshot of PR #147; this
integration record supersedes its prior Babel Low and unconfigured-scan observations.

## CI and merge order

Frontend CI uses Node 22.22+ and npm 10, performs a clean `npm ci`, verifies the
source manifest from a full-history checkout, runs the negative controls and live audit
policy, then runs lint, typecheck, the four-file route/auth suite three times, the full
frontend suite three times, and build. The PR #148 Gitleaks workflow independently runs current
tree, full history, and its negative controls on the same commit with
`contents: read`, no report artifact, and no `continue-on-error`.

After explicit approval, the canonical integration PR can be merged alone because it
contains all source commits. PR #146, #147, and #148 should not be merged first. Do not
close those source PRs until main and its CI are re-fetched after the canonical merge.
No merge, close, retarget, or Draft removal is authorized by RAG-77.

## Rollback

- Before merge: keep the canonical Draft PR unmerged.
- After an explicitly approved merge: revert the integration-specific commits or return
  to the recorded PR #147 rollback SHA and rebuild frontend assets.
- Do not reset or delete PostgreSQL, Qdrant, Neo4j, Docker volumes, LM Studio, Qwen,
  Gold v2, the normal-accuracy profile, or the RAG-31 security profile.
