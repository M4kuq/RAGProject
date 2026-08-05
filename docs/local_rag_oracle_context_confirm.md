# RAG-79 Oracle Context 3-repeat confirmation

## Scope and recovery point

This diagnostic is restricted to `local_accuracy_dev_v1`. It starts from PR #131
head `a50cc917d698ce7ab699ad5de8c4a1c991ec6ed7` and does not change PR #128,
`feature/local-rag-accuracy`, the frozen A3 profile, Gold v2, or any security profile.
The recovery point is to leave the Draft PR unmerged or revert the isolated RAG-79
commit. No database, Qdrant, Neo4j, Docker volume, or LM Studio model is reset.

## Frozen coordinates

- source evaluation run: `112`
- dataset: `local_accuracy_dev_v1`, 40 cases
- generator: exact identifier `qwen/qwen3.5-9b`
- temperature: `0`
- reasoning/planning output: disabled by the existing generation contract
- prompt profile: `baseline`
- max context: 6,000 characters
- max output: 12,000 characters / 8,192 provider tokens
- actual condition: frozen answers and the stable three-repeat Judge replay
- Oracle condition: expected dev evidence sources replace only retrieved context
- confirm repeats: 3, independent from the earlier one-repeat screening

The service rejects a dataset, case-set, corpus, generation, prompt, or Judge replay
fingerprint mismatch before aggregation. Raw question, answer, context, chunk, expected
answer, PII, and secret values are excluded from the output contract.

## Interpretation contract

The comparison keeps three evidence layers separate:

1. retrieval-only diagnostics: Claim Recall and Context Utilization;
2. actual versus Oracle end-to-end case-majority verdicts;
3. hash-bound manual-content-review calibration and paired statistics.

`Oracle pass / actual fail` is the Oracle rescue count. It is subdivided into missing
required evidence (`R Claim Recall < 1`) and evidence already retrieved but not used or
obscured by context noise (`R Claim Recall = 1`). `1 - Oracle pass` is the
generation/answerability gap, including cases that passed actual but failed Oracle.

Claim Recall and Context Utilization are additive diagnostics. Their deterministic
statement matcher does not replace Calibrated Grounded Answer Pass Rate. The manual
review is applied only when `case_id` and `answer_hash` match exactly. The available
review is Codex-assisted and explicitly requires human signoff, so it can classify
string/Judge bias but cannot support a public accuracy or profile-promotion claim.

Screening, three-repeat confirmation, manual calibration, 10,000-sample paired
bootstrap confidence intervals, and exact McNemar are reported separately. Security
fixture results are not included in the accuracy score.

## Decision table

| Observation | Next single-coordinate candidate |
|---|---|
| Oracle rescues are dominated by missing evidence | retrieval/chunk/rerank/context construction |
| Oracle remains low with complete evidence | generation/answerability/output constraint |
| Manual semantic decisions pass while exact/Judge decisions fail | evaluator fact segmentation/rubric |
| None dominates or confidence remains weak | no promotion; gather bounded calibration evidence |

## Measurement result

The three repeats completed on 2026-08-05. The raw-free local artifact SHA-256 is
`d51a5d03862267caee2941f0ed3ad8e5fdc0c3c14703f5bb385244f51b6d4a2b`. The
artifact is intentionally not committed. It contains case identifiers, hashes,
tags, counts, verdicts, and reason codes, but no raw question, answer, context, or
expected-answer text.

| Repeat | Oracle auxiliary Pass | Pipeline failure | Service duration |
|---:|---:|---:|---:|
| 1 | 29/40 | 0 | 2,158.299 s |
| 2 | 29/40 | 1 (`oracle_generation_http_400`) | 2,156.160 s |
| 3 | 30/40 | 0 | 2,012.533 s |

Repeat 2 failed generation for `local_dev_answerable_09`. The other two repeats
passed that case, so all 40 case-majority verdicts are resolvable, but the failure is
not removed from the denominator and the confirm gate is **failed**. No fourth run is
substituted after observing the failure.

### Actual versus Oracle case-majority

| Metric | Actual | Oracle majority | Delta |
|---|---:|---:|---:|
| Auxiliary Pass | 27/40 (67.5%) | 30/40 (75.0%) | +7.5 pp |
| Answerable | 18/24 | 18/24 | 0.0 pp |
| Unanswerable | 9/16 | 12/16 | +18.75 pp |
| Single-hop | 14/20 | 16/20 | +10.0 pp |
| Multi-hop | 13/20 | 14/20 | +5.0 pp |
| Japanese | 15/20 | 15/20 | 0.0 pp |
| English | 12/20 | 15/20 | +15.0 pp |

The 10,000-sample paired-bootstrap 95% confidence interval is `[-5.0, +20.0]`
percentage points and exact McNemar is `p=0.453125`. The interval crosses zero, so
the observed Oracle delta is diagnostic rather than an accuracy-improvement claim.
Oracle verdicts were stable for 38/40 cases; the unstable cases were
`local_dev_answerable_09` and `local_dev_answerable_16`.

### Gap decomposition

- Oracle rescues (`Oracle pass / actual fail`): 5
- missing-evidence retrieval gaps (`R Claim Recall < 1`): 0
- answerable retrieved-but-unused/context-noise rescues (`R Claim Recall = 1`): 2
- unanswerable context/abstention rescues: 3
- Oracle regressions (`actual pass / Oracle fail`): 2
- answerable generation gaps (`1 - Oracle pass`): 6/24
- unanswerable answerability gaps (`1 - Oracle pass`): 4/16
- actual mean Claim Recall: `0.875`
- Oracle mean Claim Recall: `1.000`
- actual deterministic Context Utilization: `0.333333`
- Oracle deterministic Context Utilization: `0.277778`

Complete Oracle evidence eliminated the missing-evidence retrieval category but left
10/40 generation/answerability failures and did not improve answerable pass rate.
Therefore retrieval/chunk expansion is not the next change. The dominant system
failure class is generation/answerability, especially using multiple available facts.

### Hash-bound manual-content-review calibration

The existing Codex-assisted manual review matched every eligible baseline answer
hash: 39/39 repeat-observations across 13 unique cases. Within its 27 reviewed
answerable observations, manual semantic review identified 27 supported atomic
claims while the deterministic whole-statement matcher identified 0. This produces
27 string-matcher false negatives and no string-matcher false positives. The
auxiliary Judge disagreed with the hash-bound manual decision on 23/39 observations
(15 false negatives and 8 false positives).

This is strong evidence that whole-string `expected_answer_slots`/statement matching
and the auxiliary Judge both contribute evaluator bias. It does not erase the seven
manually confirmed generation failures in the reviewed answerable subset. The review
status remains `requires_human_signoff`; these values are not a human-approved public
metric.

### Runtime limit and decision

The shared LM Studio model list changed from eight to seven while the run was in
progress, without any model operation by this task. Exact `qwen/qwen3.5-9b` remained
loaded, and generation coordinates remained unchanged. Because shared load changed,
the repeat durations are non-comparable operational observations and are not a
latency gate.

Decision: **diagnosis complete, confirmation gate failed, no profile promotion**.
The next single-coordinate ablation is fixed-Oracle multi-fact evidence grouping:
keep model, prompt, output budget, question, source set, and source text unchanged;
change only grouping of the two required evidence facts for multi-hop answerable
cases. This tests context utilization before changing retrieval or trying another
prompt candidate. Evaluator fact segmentation remains a separate follow-up and must
not be mixed into that generation ablation.
