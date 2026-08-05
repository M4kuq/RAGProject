# Repository-wide secret scan gate

## Decision

Gitleaks 8.30.1 extends the default rules and scans both the current tree and full Git
history. The baseline full-history scan had three synthetic/documentation findings and
zero findings classified as usable credentials. Only those three immutable historical
fingerprints are ignored. Current-tree findings are remediated in source.

This result means: the three known findings were narrowly classified as synthetic, and
all other unignored findings are zero. It does not claim that repository history never
contained secret-like text.

## Scanner contract

- Version: `8.30.1`
- Release commit: `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`
- Container digest:
  `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`
- Configuration: `[extend] useDefault = true`; no custom allowlist or relaxed rule
- History: checkout `fetch-depth: 0`
- Output: `--redact=100`; no report or artifact is written
- Failure: any unignored finding exits nonzero and fails CI

## Raw-free finding matrix

| Scope | Rule | Path | Origin commit / line | Fingerprint SHA-256 | Classification | Remediation | Credential usability |
| --- | --- | --- | --- | --- | --- | --- | --- |
| history + prior tree | `generic-api-key` | `backend/tests/test_graph_citations.py` | `8c5c8473f8068436ec3b58d636ef3f9223afc797` / 114 | `17f156f73af69c973b04af4a9c4d3252f251441a9169fc452f3233e209746eab` | synthetic sanitizer test path identifier | deterministic runtime reconstruction; exact historical fingerprint only | no credential consumer or provider transport |
| history + prior tree | `generic-api-key` | `docs/phase2/README.md` | `d4c80c6597e71692b176451bafe4560dadafd0d8` / 283 | `40f0fecb214c8c2fa4f3c413ce20b6eb05deba3ae0bdfb1a96548e2ce4e53bdb` | documentation prose about offline execution | current prose describes no external-provider contact; exact historical fingerprint only | prose, not an assigned value |
| history | `generic-api-key` | `docs/phase2/README.md` | `9a2db9dcffc0f08561b1cffe869ee71c709483c4` / 206 | `7b31489bed8689396722e24bdb2f586ab69a9fc7a7c7a4cec81a51591e3df679` | earlier documentation prose | exact historical fingerprint only | prose, not an assigned value |
| prior tree only | `private-key` | `backend/tests/test_mcp_server.py` | current-tree fixture / prior line 497 | not ignored | synthetic redaction payload | deterministic runtime reconstruction | test data is passed only to the local redactor |

The `.gitleaksignore` entries are the exact `commit:path:rule:line` identifiers for the
three history rows. It contains no path glob, wildcard, rule-wide suppression, regular
expression, entropy relaxation, or stopword.

## Verification contract

`scripts/secret_scan_gate.py` runs the following without saving scanner output:

1. Reject broad config allowlists, rule-wide ignores, and wildcard ignores.
2. Require the exact three reviewed history fingerprints.
3. Scan the current tree and full repository history.
4. Generate a deterministic secret-like mutation from fragments in a temporary Git
   repository and require detection.
5. Add the mutation at the previously affected path in a new commit and line, and
   require detection despite the exact historical ignore.
6. Remove one exact historical fingerprint and require the known finding to return.
7. Confirm generated matched text does not appear in captured scanner output.

Promotion requires configured current-tree and full-history findings to be zero, all
three negative controls to be detected, and CI to pass.

## Rollback

Before merge, leave the Draft PR unmerged. After merge, revert the isolated secret-scan
commit and return to the previous scan operation. No data store, runtime provider,
evaluation profile, or model state is changed by this gate.
