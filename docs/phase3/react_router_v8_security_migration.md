# React Router v8 security migration feasibility

## Scope and authority

- Work item: RAG-75
- Stack base: PR #146 head `d3386e37f0e2849e7ab446b61e31f8794674b384`
- Package authority: `frontend/package-lock.json` (`lockfileVersion: 3`)
- Verification runtime: Node.js 22.22.3 / npm 10.9.8 from `node:22.22-alpine`
- Audit date: 2026-08-05

Raw `npm audit` JSON and raw application data are not stored. The evidence below is limited to advisory identifiers, aggregate severities, package versions, stable test counts, and reasoned reachability decisions.

## Official requirements and advisory boundary

React Router's [v8 changelog](https://reactrouter.com/home/changelog#v800) sets Node 22.22.0+, React 19.2.7+, Vite 7+, ESM-only packages, and removal of `react-router-dom`. It directs Declarative Mode consumers to import normal DOM routing APIs from `react-router`; only `RouterProvider` and `HydratedRouter` move to `react-router/dom`. This application uses neither of those two APIs and remains a client-only Declarative Mode `BrowserRouter` application.

The security floor is not v7:

- [GHSA-wrjc-x8rr-h8h6](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6) and [GHSA-337j-9hxr-rhxg](https://github.com/advisories/GHSA-337j-9hxr-rhxg) are fixed in 7.18.0.
- [GHSA-jjmj-jmhj-qwj2](https://github.com/advisories/GHSA-jjmj-jmhj-qwj2) has no patched `react-router-dom` 6 release; the corresponding `react-router` range is fixed in 7.13.0.
- [GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2) affects `react-router` 7.12.0 through versions before 8.3.0 and is fixed in 8.3.0. The vulnerable code requires unstable RSC, which this application does not use, but a v7 dependency tree still fails the package promotion gate.

## Candidate decision

| Candidate | Isolated result | Decision |
|---|---|---|
| A: PR #146, Router 6.30.4 plus same-origin gate | Full audit Low 1 / Moderate 2; production audit Moderate 2. Open-redirect input is application-mitigated, but the package findings remain. | Safe rollback point and compensating-control baseline; not complete package remediation. |
| B: Router 7.18.2 | A temporary three-package lockfile reported High 1, GHSA-qwww. No repository file was changed for this candidate. | Rejected. It is not committed or promoted merely because RSC is unreachable. |
| C: Router 8.3.0 with the required baseline | Full audit Low 1 / Moderate 0 / High 0 / Critical 0; production audit all severities 0. All compatibility and security gates below pass. | Promotion candidate for a stacked Draft PR. |

The remaining full-audit finding is [GHSA-4x5r-pxfx-6jf8](https://github.com/advisories/GHSA-4x5r-pxfx-6jf8) in transitive dev-only `@babel/core` 7.29.0. It is absent from the production dependency tree, no fixed Babel 7 release is listed, and the repository build only transforms trusted source. This record is retained as an explicit risk-acceptance candidate; this change does not silently accept or suppress it.

## Implemented candidate C

| Component | PR #146 baseline | Candidate C resolved version |
|---|---:|---:|
| Node.js / npm | 20.20.2 / 10.8.2 | 22.22.3 / 10.9.8 |
| React / React DOM | 18.3.1 / 18.3.1 | 19.2.8 / 19.2.8 |
| React Router | `react-router-dom` 6.30.4 | `react-router` 8.3.0; `react-router-dom` removed |
| Vite / plugin-react | 6.4.3 / 4.7.0 | 7.3.6 / 5.2.0 |
| TypeScript target / resolution | ES2020 / Node | ES2022 / Bundler |

The same-origin post-login gate remains unchanged. It continues to reject protocol-relative paths, every backslash, absolute URLs, control characters, and malformed search/hash state. No unstable RSC, Framework Mode, Data SSR, manual hydration, or new production dependency was introduced.

## Verification

- `npm ci` on Node 22.22.3/npm 10.9.8: pass.
- `npm ls --all`: no invalid, unmet peer, extraneous, or other dependency problem.
- Audit: production all severities 0; full Moderate/High/Critical 0 with the documented Babel Low 1 only.
- Dependency tree: `react-router` 8.3.0 is the sole router package; `react-router-dom`, `@remix-run/router`, Router 6.30.4, and all four targeted vulnerable ranges are absent.
- Focused route/auth suite: 4 files / 67 tests, 3 of 3 repeats pass with the existing timeout.
- Full frontend suite: 17 files / 111 tests, 3 of 3 repeats pass with the existing timeout.
- Lint, typecheck, and production build: pass.
- Route behavior: same-origin login return, protected routes, nested admin routes, unmatched/404 behavior, query/hash retention, browser back/forward, links/navigation hooks, and document-review active-state boundaries pass.
- React 19 StrictMode and asynchronous state paths: covered by the three full-suite repeats without timeout or worker changes.
- Rebuilt Compose `frontend-test` and `frontend-build`, standalone frontend build target, and Compose config: pass on Node 22.22.3.
- Built assets contain zero `react-router-dom`, `@remix-run/router`, or `6.30.4` markers.
- Gitleaks v8.30.1 reports zero findings in the tracked migration diff and the new evidence document. An unconfigured repository-wide scan still returns three pre-existing rule matches in synthetic redaction tests/documentation; redacted triage confirms zero actual credentials and zero migration-introduced findings. These scanner-level false positives are recorded rather than suppressed or presented as a clean scanner exit.
- No audit ignore, audit disable, force fix, severity threshold change, timeout relaxation, or external provider call was used.

## Rollback

- Preferred while Draft: do not merge the stacked Draft PR; PR #146 remains the unchanged recovery point.
- After an explicitly approved merge: revert the isolated RAG-75 migration commit, restore the PR #146 lockfile/runtime contract, and rebuild frontend assets.
- No database, Qdrant, Neo4j, Docker volume, LM Studio, Qwen provider, Gold v2, normal-accuracy profile, or RAG-31 security profile reset is involved.
