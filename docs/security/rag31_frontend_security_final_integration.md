# RAG-31 / frontend canonical security final integration

## Scope and decision boundary

This branch preserves the complete history of PR #145 and PR #149 in one review target. The reviewable merge commit uses PR #145 as its first parent and PR #149 as its second parent. Source pull requests and their branches remain unchanged.

The promotion phrase is limited to **combined security implementation closure ready** after every local and GitHub gate passes. It does not mean production secure, deployed, or approved for merge.

## Source state

- Jira: `RAG-78`
- main base: `96e5fc82ad2c253d000633ff9e9431e88998f74a`
- PR #141: `c2ff8bf57a674e881efc4741d32eb440d87c2bfe`
- PR #145: `9baf1c7d90683463c475abe3eecf11ebe4e92781`
- PR #149: `4bda9661e4372247616e1419144a65848aa39f9f`
- reviewable merge: `d3ca21db877d62253ab651257f7a4e4a991ff770`
- merge parents: PR #145, then PR #149
- source changed-file union: 102; intersection: 2; integration missing: 0
- intersection: `frontend/src/routes/ChatPage.tsx` and `frontend/src/routes/ChatPage.test.tsx`

## Semantic overlap resolution

The merge retained the PR #145 model-selection and egress controls in `ChatPage`: the server-owned catalog remains opt-in for external models, every external request requires request-local consent, and the backend Qwen cascade remains disabled by default. PR #149 changes the same files only to move Declarative Mode imports from `react-router-dom` to React Router 8's `react-router` package. The resulting files contain both behaviors; neither source version was selected wholesale.

The same-origin post-login redirect gate from PR #149 is also preserved. Protocol-relative, backslash-containing, control-character, absolute, and malformed destinations continue to fall back to `/chat`.

## Combined gates

- RAG-31 composite and focused tests prove that blocked or quarantined injection does not reach retrieval, egress transport, Plus escalation, or cost reservation.
- External egress remains fail-closed for consent, classification, PII, secret, re-identification, region, retention, and training policy failures.
- Trusted server-side signals remain the only Plus escalation inputs; budget denial, one-escalation, circuit breaker, completed-Flash fallback, and abstain behavior are unchanged.
- Chat model selection, request-local consent, Qwen disabled-by-default, auth redirect, and route behavior run on React 19 / Router 8 / Node 22.22.
- Dependency audit, dependency-tree, repository secret scan, Alembic, backend, frontend, Docker, and Compose gates operate on this single integration head.

The first local backend full run exposed a pre-existing nondeterministic test selector: two sessions with equal expiry timestamps could cause the revoked row to be modified instead of the active row. The integration-only test fix selects the non-revoked session explicitly. The isolated expiry test then passed three repeats and the unchanged application behavior passed the full backend rerun.

Only aggregate counts, hashes, versions, and reason codes are recorded. Evaluation payloads, model output, matched secret values, and raw dependency-audit responses are not retained.

## Merge and rollback

No merge or deploy is authorized by this work. If explicitly approved later, the canonical PR can be reviewed as the single source-containing change against main; PR #141/#145/#149 and source PRs should not be merged ahead of it. Re-fetch main and all checks immediately before any merge decision.

The immediate rollback is to leave the Draft PR unmerged. After an approved merge, revert the integration commit(s) to return to main, rebuild frontend assets, and retain the safe runtime configuration: Qwen cascade disabled, user model selection disabled, external egress denied with empty allowlists, and the internet-facing injection rollback override available. No database, Qdrant, Neo4j, or Docker volume reset is required.

## Residual risk

The combined tests use mocked or synthetic transports. They do not prove live provider compatibility, billing reconciliation, unknown prompt-injection resistance, or production deployment safety. Live provider calls and production activation require separate explicit approval.
