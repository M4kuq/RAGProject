# Frontend dependency security audit

## Scope and authority

- Work item: RAG-74
- Baseline: `main` at `96e5fc82ad2c253d000633ff9e9431e88998f74a`
- Package authority: `frontend/package-lock.json` (`lockfileVersion: 3`)
- Reproduction runtime: Node.js 20.20.2 / npm 10.8.2, matching the normal frontend CI and `node:20-alpine` Dockerfile contract
- Audit date: 2026-08-05
- External evidence: GitHub Advisory Database and package-maintainer migration/release documentation linked below

The raw `npm audit --json` response is intentionally not stored. This document keeps only advisory identifiers, versions, dependency paths, reachability decisions, aggregate counts, and stable reason descriptions.

## Decision

The Critical/High promotion gate is satisfied locally:

- `npm audit --omit=dev`: Critical 0, High 0
- full `npm audit`: Critical 0, High 0
- no advisory was ignored, no audit threshold was relaxed, and `npm audit fix --force` was not used
- no override or new production dependency was introduced
- production behavior is covered by the existing frontend suite plus a new same-origin post-login redirect regression test

Three package-level findings remain: one dev-only Low in Babel and two production Moderate findings in React Router 6. They are isolated below as residual risk and are **not** silently accepted by this change.

## Aggregate audit result

`npm audit` counts affected package nodes, not distinct GHSA records. The 14 baseline package findings contained 19 distinct advisory identifiers because several packages were affected by multiple advisories and `@vitest/mocker` / `vite-node` inherited the Vitest/Vite chains.

| Audit | Low | Moderate | High | Critical | Total |
|---|---:|---:|---:|---:|---:|
| full, before | 1 | 6 | 6 | 1 | 14 |
| full, after | 1 | 2 | 0 | 0 | 3 |
| `--omit=dev`, before | 0 | 3 | 0 | 0 | 3 |
| `--omit=dev`, after | 0 | 2 | 0 | 0 | 2 |

## Resolved dependency paths

| Package | Before | After | Directness / scope | Dependency path or execution surface |
|---|---|---|---|---|
| `@remix-run/router` | 1.23.2 | 1.23.3 | transitive production | `react-router-dom -> react-router -> @remix-run/router`; browser routing |
| `brace-expansion` | 1.1.15 / 5.0.6 | 1.1.18 / 5.0.9 | transitive dev | ESLint/minimatch and typescript-eslint/minimatch; lint/type tooling |
| `esbuild` | 0.21.5 | 0.25.12 | transitive dev | `vite -> esbuild`; dev server and build |
| `form-data` | 4.0.5 | 4.0.6 | transitive dev | `jsdom -> form-data`; tests |
| `js-yaml` | 4.2.0 | 4.3.1 | transitive dev | `eslint -> @eslint/eslintrc -> js-yaml`; lint configuration |
| `postcss` | 8.5.13 | 8.5.25 | transitive dev | `vite -> postcss`; trusted repository CSS build |
| `react-router` / `react-router-dom` | 6.30.3 | 6.30.4 | transitive/direct production | browser routing; the patch also moves `@remix-run/router` to 1.23.3 |
| `vite` | 5.4.21 | 6.4.3 | direct dev | dev server and production asset build |
| `vitest` | 2.1.9 | 4.1.10 | direct dev | test runner; the matching `@vitest/*` packages move to 4.1.10 |
| `ws` | 8.20.0 | 8.21.2 | transitive dev | `jsdom -> ws`; tests |

Vite 6 was selected because 6.4.3 is the first 6.x release outside the three current Vite advisory ranges. The repository does not use the low-level/experimental APIs called out by the [Vite 5 to 6 migration guide](https://v6.vite.dev/guide/migration), and its plugin-react peer range accepts Vite 6. Vitest 3.2.6, the minimum fixed 3.x release, was tested first but repeatedly produced random five-second timeouts in the existing jsdom suite. The lockfile therefore resolves the current 4.1 patch, Vitest 4.1.10; the 4.x security floor is 4.1.0 and the line formally supports Node 20 plus Vite 6. The repository does not use the removed/deprecated APIs listed by the [Vitest 4 migration guide](https://vitest.dev/guide/migration). The suite also uses one worker without increasing the timeout, following the maintainer's [resource-contention analysis](https://github.com/vitest-dev/vitest/issues/7871). This trades test duration for deterministic CI behavior.

## Advisory-by-advisory reachability

| Advisory | Severity / CVSS | Installed before -> after; fixed boundary | Reachability and concrete scenario | Resolution |
|---|---|---|---|---|
| [GHSA-4x5r-pxfx-6jf8](https://github.com/advisories/GHSA-4x5r-pxfx-6jf8) / CVE-2026-49356 | Low / 3.2 | `@babel/core` 7.29.0 -> 7.29.0; no fixed Babel 7 release is listed | Transitive dev-only through plugin-react and eslint-plugin-react-hooks. It could read a local file when Babel processes attacker-controlled source containing a crafted `sourceMappingURL`. This build only transforms trusted repository source; it is absent from the production dependency tree and browser runtime. | Residual dev Low. Babel 8 is a separate major migration; no risk acceptance is made here. |
| [GHSA-2j2x-hqr9-3h42](https://github.com/advisories/GHSA-2j2x-hqr9-3h42) / CVE-2026-40181 | Moderate / not scored | `@remix-run/router` 1.23.2 -> 1.23.3 and `react-router` 6.30.3 -> 6.30.4 | Production browser routing. A same-origin path beginning with `//` could be reinterpreted as a protocol-relative external redirect. | Fixed dependency versions; post-login redirect validation also rejects protocol-relative paths. |
| [GHSA-3jxr-9vmj-r5cp](https://github.com/advisories/GHSA-3jxr-9vmj-r5cp) / CVE-2026-13149 | High / 5.3 | `brace-expansion` 1.1.15 / 5.0.6 -> 1.1.18 / 5.0.9; floors 1.1.16 / 5.0.7 | Dev-only lint/type globs. Crafted consecutive brace groups can cause exponential CPU work, but public request data does not feed these repository-owned globs. | Fixed on both installed paths. |
| [GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg) / CVE-2026-14257 | High / 7.5 | same paths; floors 1.1.17 / 5.0.8 | Dev-only lint/type globs. Unbounded expansion could exhaust the tooling process memory. | Fixed on both installed paths. |
| [GHSA-rgw5-rvv9-x895](https://github.com/advisories/GHSA-rgw5-rvv9-x895) / CVE-2026-69152 | High / 7.5 | same paths; floors 1.1.18 / 5.0.9 | Dev-only lint/type globs. Intermediate-array growth bypasses the earlier memory limit and can crash tooling. | Fixed on both installed paths. |
| [GHSA-67mh-4wv8-2f99](https://github.com/advisories/GHSA-67mh-4wv8-2f99) | Moderate / 5.3 | `esbuild` 0.21.5 -> 0.25.12; vulnerable through 0.24.2 | Vite dev server only. A malicious website could issue requests to, and read responses from, an exposed dev server. `npm run dev` binds broadly, so this is reachable on a developer network if that port is exposed; it is not bundled into production assets. | Fixed through Vite 6.4.3. |
| [GHSA-hmw2-7cc7-3qxx](https://github.com/advisories/GHSA-hmw2-7cc7-3qxx) / CVE-2026-12143 | High / 7.5 | `form-data` 4.0.5 -> 4.0.6; floor 4.0.6 | Dev-only through jsdom. An attacker-controlled multipart field name or filename could inject CRLF into an outbound multipart body. Tests do not forward public input to live services. | Fixed transitively. |
| [GHSA-52cp-r559-cp3m](https://github.com/advisories/GHSA-52cp-r559-cp3m) / CVE-2026-59869 | High / 7.5 | `js-yaml` 4.2.0 -> 4.3.1; floor 4.3.0 | Dev-only ESLint configuration parsing. Crafted YAML merge-key chains could cause quadratic CPU use; only repository-owned configuration is parsed. | Fixed transitively. |
| [GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849) | High / 7.5 | `postcss` 8.5.13 -> 8.5.25; vulnerable through 8.5.17 | Build/dev-only through Vite. Attacker-controlled CSS `sourceMappingURL` could traverse to and disclose local `.map` files. The build accepts repository CSS, not uploaded/user CSS. | Fixed transitively. |
| [GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp) / CVE-2026-69153 | Moderate / not scored | same path; vulnerable through 8.5.22 | Build/dev-only. This incomplete-fix bypass can read `.map` files when PostCSS is invoked without `from`; public data does not enter this build step. | Fixed transitively. |
| [GHSA-wrjc-x8rr-h8h6](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6) / CVE-2026-53669 | Moderate / not scored | `react-router` 6.30.3 -> 6.30.4; package fix requires 7.18.0+ | Production browser bundle. Crafted paths containing backslashes can turn attacker-supplied navigation into an external redirect. The login return path was the only navigation target derived from location state. | Residual package alert, mitigated in application code by rejecting `//`, every backslash, and control characters before navigation. Other dynamic routes are server-owned numeric identifiers. |
| [GHSA-337j-9hxr-rhxg](https://github.com/advisories/GHSA-337j-9hxr-rhxg) / CVE-2026-53666 | Moderate / 6.1 | `react-router` 6.30.3 -> 6.30.4; package fix requires 7.18.0+ | Not reachable in this application. The advisory requires Framework/Data Mode manual SSR hydration and attacker influence over serialized errors; this frontend uses declarative `BrowserRouter` with no SSR/hydration. | Residual package alert; architecture excludes the vulnerable mode. No acceptance is implied. |
| [GHSA-jjmj-jmhj-qwj2](https://github.com/advisories/GHSA-jjmj-jmhj-qwj2) / CVE-2026-53668 | Moderate / 6.9 | `react-router-dom` 6.30.3 -> 6.30.4; affected 6.30.2 through 6.30.4 | Production browser bundle. An existing open redirect can be escalated to an external navigation or XSS vector. The post-login target was potentially reachable from a crafted same-origin path. | Residual package alert, mitigated by the same-origin path gate and six focused tests. A React Router major migration remains a separate review decision. |
| [GHSA-4w7w-66w2-5vf9](https://github.com/advisories/GHSA-4w7w-66w2-5vf9) / CVE-2026-39365 | Moderate / not scored | `vite` 5.4.21 -> 6.4.3; floor 6.4.2 | Dev server only. Crafted optimized-dependency source-map paths could traverse outside the expected map location and disclose files. | Fixed by direct Vite update. |
| [GHSA-v6wh-96g9-6wx3](https://github.com/advisories/GHSA-v6wh-96g9-6wx3) / CVE-2026-53632 | Moderate / not scored | same path; floor 6.4.3 | Dev server on Windows. A crafted UNC path passed to launch-editor could disclose the developer's NTLMv2 hash. It is absent from static production assets. | Fixed by direct Vite update. |
| [GHSA-fx2h-pf6j-xcff](https://github.com/advisories/GHSA-fx2h-pf6j-xcff) / CVE-2026-53571 | High / 7.5 | same path; floor 6.4.3 | Dev server on Windows. Alternate path syntax could bypass `server.fs.deny` and read a denied local file. Broad dev binding made this potentially reachable on an exposed developer network, though `allowedHosts` narrowed accepted hosts. | Fixed by direct Vite update. |
| [GHSA-5xrq-8626-4rwp](https://github.com/advisories/GHSA-5xrq-8626-4rwp) / CVE-2026-47429 | Critical / 9.8 | `vitest` 2.1.9 -> 4.1.10; fixed floors 3.2.6 / 4.1.0 | Dev/test-only. The vulnerable Vitest UI server can read and execute arbitrary files when listening. This repository uses `vitest run` and does not install or expose `@vitest/ui`, so the vulnerable server was not active; Critical was nevertheless removed. | Fixed by isolated Vitest major update; 109 tests verify compatibility. |
| [GHSA-58qx-3vcg-4xpx](https://github.com/advisories/GHSA-58qx-3vcg-4xpx) / CVE-2026-45736 | Moderate / 4.4 | `ws` 8.20.0 -> 8.21.2; floor 8.20.1 | Dev-only through jsdom. A peer could receive uninitialized memory from a malformed WebSocket interaction; no jsdom WebSocket server is exposed to public traffic. | Fixed transitively. |
| [GHSA-96hv-2xvq-fx4p](https://github.com/advisories/GHSA-96hv-2xvq-fx4p) / CVE-2026-48779 | High / 7.5 | same path; floor 8.21.0 | Dev-only through jsdom. Tiny fragments/data chunks can exhaust memory in a WebSocket process; no production dependency path exists. | Fixed transitively. |

## Residual risk boundary

1. `@babel/core` 7.29.0 remains a dev-only Low finding because the advisory provides no patched Babel 7 release. The current build does not transform attacker-supplied source. Upgrade to stable Babel 8 must be reviewed separately before any multi-tenant/untrusted-source build workflow is introduced.
2. React Router 6.30.4 remains two production Moderate package findings covering three GHSA records. The application now blocks the reachable open-redirect primitive, and its declarative client-only architecture excludes the SSR constructor-injection scenario. This is compensating control, not package-risk acceptance. Migration to a currently supported React Router major must be separately reviewed and retested before internet-facing release.
3. The production bundle still contains React Router 6 by design; no vulnerable production Critical/High package remains. Vite, Vitest, Babel, lint, jsdom, and their affected transitives remain dev-only lock entries and are not production dependencies.

## Verification contract

- Clean install: `npm ci` in Node 20 succeeds from the committed lockfile.
- Dependency tree: `npm ls --all --json` exits 0 with no `problems` field and no invalid, unmet peer, or extraneous markers.
- Frontend: lint, typecheck, 109 tests, and production build must pass in the rebuilt Node 20 image.
- Docker/Compose: the `frontend/Dockerfile` build target and rebuilt `frontend-test` / `frontend-build` services must pass.
- Secret scan: a redacted Gitleaks scan is required before publication; no report containing matched material is committed.
- GitHub CI: all required checks on the Draft PR must pass before RAG-74 moves to review.

## Rollback

- Preferred while Draft: do not merge the Draft PR.
- After an explicitly approved merge: revert the isolated dependency/security commit and rebuild frontend assets from the previous lockfile.
- No database, Qdrant, Neo4j, Docker volume, LM Studio, model provider, Gold v2, normal-accuracy profile, or security profile reset is part of this rollback.
