# RAG-89: RAG-88 timeout code-path root-cause diagnostic

## Decision

RAG-88 remains `inconclusive / rag88_pipeline_failure`; its fixture, result, prompt,
thresholds, 180-second timeout, and 6000 / 12000 / 8192 budgets are unchanged. No live
model call or RAG-88 rerun was performed.

The best-supported primary cause is a mismatch between the fixed 180-second logical-call
deadline and the measured standard-generation latency distribution. Confidence is high:

- 40 direct failures all carry
  `rag88_review_generation_case_wall_clock_timeout`.
- Their recorded latency range is 180,015--180,120 ms.
- The 68 successful standard calls have p50 / p75 / p90 / p95 latencies of
  115,300 / 130,574 / 154,130 / 159,420 ms and a maximum of 179,741 ms.
- Nine successful standard calls took at least 150 seconds; two took at least 170 seconds.
- The 25 completed repair calls are much shorter: p50 10,602 ms, p95 12,084 ms,
  maximum 12,315 ms.

Physical-call amplification is present in the design, but its contribution to the recorded
timeouts cannot be quantified because the fixed artifact has no standard-generation retry
counter. An application cancellation leak is not established. LM Studio behavior after a
client disconnect is also not observable from the fixed raw-free evidence.

## Evidence binding

- RAG-88 source code commit: `6f526ec27a3c79da0d7642501348393aa89b198b`
- RAG-88 pre-live commit: `f222ea8538feba8872108012e37571d9aaef5ecf`
- Result SHA-256: `dca0865277b40f1c05401ca3afc74798c3a69b01b7e8ea161c819e3b6d2954e9`
- Attempt SHA-256: `ffac4be6bcc021791937835f105771a955159ee47b47ff6926fb9e22555591c7`
- RAG-89 aggregate diagnostic SHA-256:
  `6222ece91f2ec4ca8fcbf394df50d64a3167dec7563b9c23bfa375115ce6be38`
- RAG-88 observations: 144 / 144; raw content and chain-of-thought persisted: false / false

Only repeat, variant, execution ordinal, duration, repair duration, stable reason code, and
counters were projected. Question, source, context, answer, fact, model output, headers, and
request payload were neither emitted nor added to the repository.

## Failure reconstruction

Direct timeout counts by repeat are 15 / 12 / 13. Derived candidate-unavailable counts are
5 / 3 / 3. The direct distribution is:

| Repeat | single-A | single-B | combined baseline |
|---:|---:|---:|---:|
| 1 | 5 | 5 | 5 |
| 2 | 3 | 6 | 3 |
| 3 | 5 | 5 | 3 |

Every one of the 11 `rag88_candidate_pass1_unavailable` observations immediately follows
the paired timed-out combined baseline and copies its latency. It does not start another
HTTP request. Thus 51 is the logical-observation failure count: 40 direct timeouts plus 11
single derived observations, not 51 independent physical request failures.

The physical-accounted duration is 14,460,586 ms (about 4.017 hours). It sums standard-call
latencies and actual repair latencies once; it excludes the candidate observation's copied
baseline latency. It is an artifact-derived accounting value, not a separate wall-clock log.

## Logical and physical calls

The fixed schedule contains 144 logical observations:

- single-A: 36
- single-B: 36
- combined baseline: 36
- combined candidate: 36

There are 108 standard logical calls. Each performs one initial HTTP generation. The shared
`generate_evaluation_answer` policy can perform an empty-result retry and then a citation
retry, so the static upper bound is three physical HTTP requests per standard logical call.
There is no retry backoff.

The candidate reuses the paired combined-baseline result as pass 1; it does not generate a
duplicate pass 1. If pass 1 is available, the coverage/revision check makes exactly one
physical request and performs at most one revision within that returned structured result.
The repair path has no internal retry. In the fixed result, 25 candidate repair requests ran
and 11 were skipped because pass 1 was unavailable.

Therefore the fixed result's physical HTTP request count is bounded as follows:

- minimum: `108 initial standard + 25 repair = 133`
- maximum: `108 * 3 standard attempts + 25 repair = 349`
- exact count: unavailable because standard retry counters were not persisted

The full healthy design bound is 360 requests (`108 * 3 + 36`). The repository field named
`model_call_count=144` represents logical schedule positions, not an audited physical HTTP
request count.

## 180-second scope

For a standard logical call, the parent starts a spawned worker and joins it for 180 seconds.
That single deadline includes worker startup, request construction, all physical retry
attempts, connection/pool/write/header wait, provider generation, full response-body read,
parse and citation validation, and pipe delivery. Parse and citation validation occur after
the HTTP response is fully read but still inside the same parent deadline.

The HTTP client also receives `timeout=180`. With the pinned HTTPX 0.28.1 implementation,
this expands to separate 180-second connect, read, write, and pool operation timeouts; it is
not a total request deadline. The parent process deadline is therefore the effective total
logical-call cap and can terminate a later retry before that retry's own HTTP timeout.

Candidate repair runs in a separate spawned worker with a new 180-second deadline. There is
no single 180-second deadline around combined baseline plus repair; reported candidate
latency is their sum. There is no retry/backoff phase in the repair path.

The fixed evidence does not distinguish provider queue time from connect/header wait,
generation, or body read. That missing phase attribution is recorded as
`rag89_timeout_phase_unobserved`, not guessed.

## Cancellation and cleanup

On parent deadline, the code performs `terminate`, waits up to five seconds, then uses `kill`
and waits again only if the child remains alive. The focused deterministic process test
verifies the transport is stopped on termination and no child remains. The existing real
spawned-process test also verifies that the timed-out child is not alive after cleanup.

Within a normally running child, top-level `httpx.post` creates a new client for each request,
fully reads a non-streaming response, closes the response on read failure, and closes the
client context before returning. No application session or connection pool is shared with
the next logical call. Deterministic fake transport/clock tests cover delayed headers, slow
stream, partial body, response close, and a successful request immediately after a timeout.

Forced process termination is not cooperative HTTP cancellation, but process exit closes
the application's socket handles. Whether LM Studio cancels its server-side generation on
that disconnect or continues consuming its single-model queue is not present in the fixed
artifact and was not tested with a live provider. It remains
`rag89_provider_disconnect_behavior_unobserved`.

## Root-cause separation

| Category | Finding | Confidence |
|---|---|---|
| Fixed budget vs measured throughput | Established primary mismatch: 40 calls hit the parent deadline and the successful tail reaches 179.741 s | High |
| Physical-call amplification | Design established: up to three standard requests; actual retry contribution unknown | Medium |
| Application timeout/cancellation bug | Not established: child is force-terminated, joined, and not reused; no app pool/session survives | Medium |
| LM Studio/provider behavior | Provider-side continuation or queue blocking after disconnect is unobserved | Insufficient |

The defensible conclusion is not that LM Studio definitely continued timed-out generations,
nor that retries definitely caused the 40 timeouts. The fixed deadline/throughput mismatch is
directly measured; amplification and provider behavior are residual mechanisms requiring new
telemetry in a future independent run.

## Verification contract

Focused tests use no live model or external network. They cover:

- delayed headers;
- slow response stream;
- partial body just before timeout;
- response/transport close after timeout;
- a following request not being blocked by the prior application's client state;
- three-request upper bound for standard retry policy;
- one-request upper bound for repair;
- exactly one candidate-unavailable observation derived from one timed-out pass 1.

Verification results:

- focused RAG-88 / timeout-process / generator set: 55 passed / 3 skipped;
- full backend: 1,116 passed / 21 skipped;
- Ruff: check passed and 319 files formatted;
- mypy app scope: 213 source files passed;
- isolated Compose: Ruff passed; mypy 319 source files passed;
- isolated Compose full pytest attempt 1: 1,115 passed / 21 skipped with one pre-existing
  SQLite finished/started timestamp-boundary failure; that exact test passed alone;
- isolated Compose full pytest attempt 2: 1,114 passed / 21 skipped with one pre-existing
  SQLite message-order timestamp tie and one finished/started timestamp-boundary failure;
  all three affected parameterized/single cases passed immediately in isolation.

The Compose full-suite failures do not touch the RAG-89 files and did not reproduce in the
green full-backend run. They are retained as validation risk rather than hidden by repeated
full-suite execution. No PostgreSQL, Qdrant, or Neo4j service was started. The Compose test
used container-local SQLite only, created no named volume, and finished with task containers,
volumes, and attached network endpoints at 0 / 0 / 0. Docker's exhausted default address
pool blocked the first service start; a dedicated `10.255.89.0/24` task network was used and
removed after verification without deleting an existing network.

No Gold v2, LM Studio state change, datastore, or application volume was required. GitHub CI
is recorded separately after the Draft PR is opened.

## Next live goal: one coordinate only

Change only the **timeout contract** in a newly pre-registered independent fixture. This is
the only coordinate directly supported by the current timing evidence. Do not combine it
with cancellation changes, physical-call reduction, output-budget reduction, prompt changes,
or RAG-88 fixture reuse. Commit the independent fixture and its one-shot gate before any live
call. RAG-88 itself remains non-rerunnable.

## Rollback

Revert the single RAG-89 diagnostic commit. No datastore, model, fixture-result, or runtime
state rollback is required because this diagnostic performs no live call and writes no
application datastore.
