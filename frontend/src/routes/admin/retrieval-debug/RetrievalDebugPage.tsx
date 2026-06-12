import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { useEvaluationRuns } from "../../../features/evaluations/evaluationHooks";
import type { StrategyComparisonMetric } from "../../../features/evaluations/evaluationTypes";
import { formatUnknownValue, redactString, safeRecord } from "../../../features/retrievalDebug/redaction";
import {
  useRagDebugSearch,
  useRetrievalRunDebugHistory,
  useRetrievalRunDebugDetail
} from "../../../features/retrievalDebug/retrievalDebugHooks";
import type {
  ContextBudgetItemRef,
  ContextBudgetTrace,
  DroppedEvidenceRef,
  EvidenceItemRef,
  EvidencePackTrace,
  RagSearchDebugItem,
  RetrievalRunDebugDetail,
  RetrievalRunDebugItem,
  RetrievalRunDebugSummary,
  SupportedRetrievalDebugStrategy,
  ToolResultCompressionTrace,
  ToolResultItemRef
} from "../../../features/retrievalDebug/retrievalDebugTypes";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

const SUPPORTED_STRATEGIES: Array<{ value: SupportedRetrievalDebugStrategy; label: string }> = [
  { value: "dense", label: "dense" },
  { value: "sparse", label: "sparse" },
  { value: "hybrid", label: "hybrid" },
  { value: "agentic_router", label: "agentic_router" }
];

const FUTURE_STRATEGIES = [
  "multi_query_dense",
  "multi_query_hybrid",
  "metadata_filtered",
  "version_aware"
];

const LATENCY_KEYS = [
  "total_ms",
  "retrieval_ms",
  "agentic_total_ms",
  "langchain_agentic_ms",
  "langchain_planning_ms",
  "langchain_tool_execution_ms",
  "initial_retrieval_ms",
  "fallback_retrieval_ms",
  "sufficiency_check_ms",
  "merge_dedupe_ms",
  "rerank_after_merge_ms",
  "query_embedding_ms",
  "qdrant_search_ms",
  "sparse_search_ms",
  "fusion_ms",
  "strategy_router_ms",
  "rdb_final_check_ms",
  "rerank_ms",
  "retrieval_items_persist_ms",
  "generation_ms",
  "evidence_pack_ms",
  "citation_build_ms",
  "confidence_ms"
];

const EVALUATION_METRICS = [
  "recall_at_k",
  "mrr",
  "no_context_rate",
  "p95_latency",
  "citation_coverage",
  "groundedness"
];

type DisplayItem = {
  key: string;
  searchItem: RagSearchDebugItem | null;
  detailItem: RetrievalRunDebugItem | null;
};

export function RetrievalDebugPage() {
  const [query, setQuery] = useState("");
  const [strategy, setStrategy] = useState<SupportedRetrievalDebugStrategy>("dense");
  const [topK, setTopK] = useState(10);
  const [rerankTopN, setRerankTopN] = useState(5);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const search = useRagDebugSearch();
  const history = useRetrievalRunDebugHistory();
  const latestRunId = selectedRunId ?? history.data?.items[0]?.retrieval_run_id ?? null;
  const detail = useRetrievalRunDebugDetail(latestRunId);
  const evaluations = useEvaluationRuns({ page: 1, page_size: 5 });
  const searchItems =
    latestRunId !== null && latestRunId === search.data?.retrieval_run_id ? search.data.items : [];

  const displayItems = useMemo(
    () => buildDisplayItems(searchItems, detail.data?.items ?? []),
    [searchItems, detail.data?.items]
  );

  useEffect(() => {
    if (selectedRunId === null && history.data?.items.length) {
      setSelectedRunId(history.data.items[0].retrieval_run_id);
    }
  }, [history.data?.items, selectedRunId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery) {
      setFormError("Query is required.");
      return;
    }
    setFormError(null);
    const safeTopK = clampNumber(topK, 1, 20, 10);
    const safeRerankTopN = clampNumber(rerankTopN, 1, 20, 5);
    const result = await search.mutateAsync({
      query: trimmedQuery,
      top_k: safeTopK,
      rerank_top_n: safeRerankTopN,
      strategy
    });
    setSelectedRunId(result.retrieval_run_id);
    void history.refetch();
  }

  async function refreshTrace() {
    await Promise.all([history.refetch(), latestRunId ? detail.refetch() : Promise.resolve()]);
  }

  return (
    <main className="admin-main retrieval-debug-page">
      <header className="page-header">
        <div>
          <h1>Retrieval Debug</h1>
          <p className="muted">Run dense, sparse, and hybrid retrieval and inspect safe trace metadata.</p>
        </div>
        <button
          type="button"
          disabled={history.isFetching || detail.isFetching}
          onClick={() => void refreshTrace()}
        >
          Refresh trace
        </button>
      </header>

      <form className="admin-section retrieval-debug-form" onSubmit={submit}>
        <label>
          query
          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            maxLength={8000}
            rows={4}
            required
          />
        </label>
        <label>
          strategy
          <select
            value={strategy}
            onChange={(event) => setStrategy(event.target.value as SupportedRetrievalDebugStrategy)}
          >
            {SUPPORTED_STRATEGIES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          top_k
          <input
            type="number"
            min={1}
            max={20}
            value={topK}
            onChange={(event) => setTopK(Number(event.target.value))}
          />
        </label>
        <label>
          rerank_top_n
          <input
            type="number"
            min={1}
            max={20}
            value={rerankTopN}
            onChange={(event) => setRerankTopN(Number(event.target.value))}
          />
        </label>
        <button type="submit" disabled={search.isPending}>
          Run search
        </button>
        <div className="field-group unsupported-strategy-list">
          coming soon
          <span className="inline-options">
            {FUTURE_STRATEGIES.map((item) => (
              <button key={item} type="button" disabled title="Coming soon">
                {item}
              </button>
            ))}
          </span>
        </div>
      </form>

      {formError ? <InlineAlert tone="error">{formError}</InlineAlert> : null}
      {search.error ? <InlineAlert tone="error">{search.error.message}</InlineAlert> : null}
      {history.error ? <ErrorState title="Unable to load retrieval run history" error={history.error} /> : null}
      {search.isPending ? <LoadingState label="Running retrieval..." /> : null}

      <RetrievalRunHistoryPanel
        isLoading={history.isLoading}
        onSelect={setSelectedRunId}
        runs={history.data?.items ?? []}
        selectedRunId={latestRunId}
      />

      {latestRunId ? (
        <>
          <SearchResultSummary detail={detail.data} retrievalRunId={latestRunId} searchData={search.data} />
          {detail.isLoading ? <LoadingState label="Loading trace..." /> : null}
          {detail.error ? <ErrorState title="Unable to load retrieval trace" error={detail.error} /> : null}
          {detail.data ? <RetrievalRunTracePanel detail={detail.data} /> : null}
          {detail.data ? <ContextBudgetPanel trace={detail.data.retrieval_run.context_budget_json} /> : null}
          {detail.data ? <EvidencePackPanel trace={detail.data.retrieval_run.context_compression_json} /> : null}
          {detail.data ? (
            <ToolResultCompressionPanel trace={detail.data.retrieval_run.tool_result_compression_json} />
          ) : null}
          <ScoreBreakdownTable items={displayItems} />
          <RetrievalRunItemsTable items={displayItems} />
        </>
      ) : (
        <EmptyState title="No retrieval runs">
          Run a retrieval search, use Chat RAG, or refresh after another retrieval to inspect trace and score breakdowns.
        </EmptyState>
      )}

      <EvaluationStrategySummaryPanel metrics={latestStrategyMetrics(evaluations.data?.items ?? [])} />
    </main>
  );
}

function SearchResultSummary({
  detail,
  retrievalRunId,
  searchData
}: {
  detail: RetrievalRunDebugDetail | undefined;
  retrievalRunId: number;
  searchData: { retrieval_run_id: number; retrieval_score_summary: Record<string, unknown>; items: unknown[] } | undefined;
}) {
  const run = detail?.retrieval_run;
  const summary = safeRecord(run?.retrieval_score_summary ?? searchData?.retrieval_score_summary);
  const decision = safeRecord(run?.strategy_decision_json);
  const preferSummaryTrace =
    run?.strategy_type === "llm_tool_orchestrator" || run?.strategy_type === "langchain_agentic";
  const isToolOrchestrator = preferSummaryTrace;
  return (
    <section className="admin-section">
      <h2>Run Summary</h2>
      {isToolOrchestrator ? (
        <p className="section-help">
          Tool orchestration uses retrieval tool calls instead of the rule-based sufficiency check.
          Review tools_used, search_call_count, and fallback_reason for the retrieval path.
        </p>
      ) : null}
      <dl className="detail-grid">
        <Detail label="retrieval_run_id" value={`#${retrievalRunId}`} />
        <Detail label="status" value={run ? <StatusBadge status={run.status} /> : "succeeded"} />
        <Detail label="strategy" value={run?.strategy_type ?? "N/A"} />
        <Detail label="selected_count" value={formatUnknownValue(summary.selected_count)} />
        <Detail
          label="rdb_final_check_excluded"
          value={formatUnknownValue(summary.excluded_by_rdb_check_count)}
        />
        <Detail
          label="fallback_used"
          value={formatUnknownValue(traceField(decision, summary, "fallback_used", false, preferSummaryTrace))}
        />
        <Detail
          label="retrieval_call_count"
          value={formatUnknownValue(traceRetrievalCallCount(decision, summary, preferSummaryTrace))}
        />
        <Detail
          label="sufficiency_score"
          value={formatScoreWithNote(
            traceField(decision, summary, "sufficiency_score", undefined, preferSummaryTrace),
            isToolOrchestrator ? "not computed for tool orchestration" : undefined
          )}
        />
        {isToolOrchestrator ? (
          <>
            <Detail label="tools_used" value={formatUnknownValue(summary.tools_used ?? decision.tools_used ?? [])} />
            <Detail
              label="search_call_count"
              value={formatUnknownValue(traceField(decision, summary, "search_call_count", undefined, true))}
            />
            <Detail
              label="fallback_reason"
              value={formatUnknownValue(traceField(decision, summary, "fallback_reason", undefined, true))}
            />
          </>
        ) : null}
        <Detail label="started" value={formatDate(run?.started_at)} />
        <Detail label="finished" value={formatDate(run?.finished_at)} />
        <Detail label="error" value={formatSafeText(run?.error_code ?? null, 80)} />
      </dl>
    </section>
  );
}

function RetrievalRunHistoryPanel({
  isLoading,
  onSelect,
  runs,
  selectedRunId
}: {
  isLoading: boolean;
  onSelect: (retrievalRunId: number) => void;
  runs: RetrievalRunDebugSummary[];
  selectedRunId: number | null;
}) {
  return (
    <section className="admin-section">
      <h2>Recent Retrieval Runs</h2>
      {isLoading ? <LoadingState label="Loading retrieval run history..." /> : null}
      {!isLoading && runs.length === 0 ? (
        <EmptyState title="No retrieval run history">No retrieval runs have been recorded yet.</EmptyState>
      ) : null}
      {runs.length ? (
        <table className="admin-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Origin</th>
              <th>Strategy</th>
              <th>Execution</th>
              <th>Selected</th>
              <th>Started</th>
              <th>Finished</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const decision = safeRecord(run.strategy_decision_json);
              const summary = safeRecord(run.retrieval_score_summary);
              return (
                <tr key={run.retrieval_run_id}>
                  <td>#{run.retrieval_run_id}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td>{run.origin_type}</td>
                  <td>{run.strategy_type}</td>
                  <td>{formatUnknownValue(decision.execution_strategy ?? decision.selected_strategy ?? "N/A")}</td>
                  <td>{formatUnknownValue(summary.selected_count ?? "N/A")}</td>
                  <td>{formatDate(run.started_at)}</td>
                  <td>{formatDate(run.finished_at)}</td>
                  <td>
                    <button
                      type="button"
                      disabled={run.retrieval_run_id === selectedRunId}
                      onClick={() => onSelect(run.retrieval_run_id)}
                    >
                      {run.retrieval_run_id === selectedRunId ? "Selected" : "View"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function RetrievalRunTracePanel({ detail }: { detail: RetrievalRunDebugDetail }) {
  const run = detail.retrieval_run;
  const queryPlan = safeRecord(run.query_plan_json);
  const analysis = recordFrom(queryPlan.analysis);
  const planner = recordFrom(queryPlan.planner);
  const subQueries = arrayFrom(queryPlan.sub_queries ?? planner.sub_queries);
  const metadataCandidates = arrayFrom(
    queryPlan.metadata_filter_candidates ?? planner.metadata_filter_candidates
  );
  const decision = safeRecord(run.strategy_decision_json);
  const settings = safeRecord(run.retrieval_settings_json);
  const latency = safeRecord(run.latency_breakdown_json);
  const summary = safeRecord(run.retrieval_score_summary);
  const preferSummaryTrace =
    run.strategy_type === "llm_tool_orchestrator" || run.strategy_type === "langchain_agentic";
  const isToolOrchestrator = preferSummaryTrace;

  return (
    <section className="admin-section retrieval-debug-grid">
      <TraceCard title="Query Plan">
        <dl className="detail-grid">
          <Detail label="query_mode" value={formatUnknownValue(queryPlan.query_mode)} />
          <Detail label="query_hash" value={shortHash(queryPlan.query_hash)} />
          <Detail label="intent" value={formatUnknownValue(queryPlan.intent ?? analysis.intent)} />
          <Detail label="ambiguity_score" value={formatScore(queryPlan.ambiguity_score ?? analysis.ambiguity_score)} />
          <Detail
            label="ambiguity_flags"
            value={formatUnknownValue(queryPlan.ambiguity_flags ?? analysis.ambiguity_flags ?? [])}
          />
          <Detail
            label="keyword_heavy_score"
            value={formatScore(queryPlan.keyword_heavy_score ?? analysis.keyword_heavy_score)}
          />
          <Detail
            label="version_specific"
            value={formatUnknownValue(queryPlan.version_specific_flag ?? analysis.version_specific_flag ?? false)}
          />
          <Detail label="rewrite_applied" value={formatUnknownValue(queryPlan.rewrite_applied ?? false)} />
          <Detail
            label="rewritten_query_preview"
            value={formatUnknownValue(queryPlan.rewritten_query_preview ?? planner.rewritten_query_preview)}
          />
          <Detail label="sub_queries" value={formatUnknownValue(queryPlan.sub_query_count ?? subQueries.length)} />
          <Detail
            label="metadata_filter"
            value={formatUnknownValue(queryPlan.metadata_filter_applied ?? false)}
          />
          <Detail label="metadata_candidates" value={formatUnknownValue(metadataCandidates.length)} />
          <Detail label="candidate_strategies" value={formatUnknownValue(queryPlan.candidate_strategies ?? [])} />
          <Detail label="recommended_strategy" value={formatUnknownValue(queryPlan.recommended_strategy)} />
          <Detail label="planned_only" value={formatUnknownValue(queryPlan.safety_flags ?? [])} />
        </dl>
        <NestedList title="Sub-query previews" items={subQueries} />
        <NestedList title="Metadata filter candidates" items={metadataCandidates} />
        <SafeDetails record={queryPlan} />
      </TraceCard>

      <TraceCard title="Strategy Decision">
        {isToolOrchestrator ? (
          <p className="section-help">
            This run was controlled by bounded retrieval tools. Router-only fields may be unavailable;
            use the tool call fields below for Agentic RAG tool behavior.
          </p>
        ) : null}
        <dl className="detail-grid">
          <Detail label="selected_strategy" value={formatUnknownValue(decision.selected_strategy)} />
          <Detail label="execution_strategy" value={formatUnknownValue(decision.execution_strategy)} />
          <Detail label="decision_source" value={formatUnknownValue(decision.decision_source ?? "default")} />
          <Detail label="router_enabled" value={formatUnknownValue(decision.router_enabled ?? false)} />
          {isToolOrchestrator ? (
            <>
              <Detail label="tools_used" value={formatUnknownValue(summary.tools_used ?? decision.tools_used ?? [])} />
              <Detail
                label="tool_call_count"
                value={formatUnknownValue(traceField(decision, summary, "tool_call_count", undefined, true))}
              />
              <Detail
                label="search_call_count"
                value={formatUnknownValue(traceField(decision, summary, "search_call_count", undefined, true))}
              />
              <Detail
                label="finalize_called"
                value={formatUnknownValue(traceField(decision, summary, "finalize_called", undefined, true))}
              />
              <Detail
                label="timeout_exceeded"
                value={formatUnknownValue(traceField(decision, summary, "timeout_exceeded", false, true))}
              />
              <Detail
                label="repeated_query_detected"
                value={formatUnknownValue(traceField(decision, summary, "repeated_query_detected", false, true))}
              />
            </>
          ) : null}
          <Detail
            label="fallback_used"
            value={formatUnknownValue(traceField(decision, summary, "fallback_used", false, preferSummaryTrace))}
          />
          <Detail
            label="fallback_strategy"
            value={formatUnknownValue(traceField(decision, summary, "fallback_strategy", "N/A", preferSummaryTrace))}
          />
          <Detail
            label="fallback_reason"
            value={formatUnknownValue(traceField(decision, summary, "fallback_reason", undefined, preferSummaryTrace))}
          />
          <Detail
            label="retrieval_call_count"
            value={formatUnknownValue(traceRetrievalCallCount(decision, summary, preferSummaryTrace))}
          />
          <Detail
            label="budget_exhausted"
            value={formatUnknownValue(traceField(decision, summary, "budget_exhausted", false, preferSummaryTrace))}
          />
          <Detail
            label="sufficiency_score"
            value={formatScoreWithNote(
              traceField(decision, summary, "sufficiency_score", undefined, preferSummaryTrace),
              isToolOrchestrator ? "not computed for tool orchestration" : undefined
            )}
          />
          <Detail
            label="sufficiency_reason_codes"
            value={formatUnknownValue(decision.sufficiency_reason_codes ?? [])}
          />
          <Detail label="initial_candidates" value={formatUnknownValue(decision.initial_candidate_count)} />
          <Detail label="merged_candidates" value={formatUnknownValue(decision.merged_candidate_count)} />
          <Detail label="deduped_candidates" value={formatUnknownValue(decision.deduped_candidate_count)} />
          <Detail label="final_selected" value={formatUnknownValue(decision.final_selected_count)} />
          <Detail label="confidence" value={formatScore(decision.confidence)} />
          <Detail label="disabled_candidates" value={formatUnknownValue(decision.disabled_candidates ?? [])} />
          <Detail label="safety_flags" value={formatUnknownValue(decision.safety_flags ?? [])} />
          <Detail label="reason_codes" value={formatUnknownValue(decision.reason_codes ?? [])} />
        </dl>
        <SafeDetails record={decision} />
      </TraceCard>

      <TraceCard title="Retrieval Settings">
        <dl className="detail-grid">
          <Detail label="top_k" value={formatUnknownValue(settings.top_k)} />
          <Detail label="rerank_top_n" value={formatUnknownValue(settings.rerank_top_n)} />
          <Detail label="embedding_provider" value={formatUnknownValue(settings.embedding_provider)} />
          <Detail label="rerank_provider" value={formatUnknownValue(settings.rerank_provider)} />
          <Detail label="fusion_method" value={formatUnknownValue(settings.fusion_method)} />
          <Detail label="router_enabled" value={formatUnknownValue(settings.router_enabled ?? false)} />
          <Detail label="max_retrieval_calls" value={formatUnknownValue(settings.max_retrieval_calls)} />
          <Detail label="max_fallback_calls" value={formatUnknownValue(settings.max_fallback_calls)} />
          <Detail
            label="sufficiency_threshold"
            value={formatScore(settings.sufficiency_top_score_threshold)}
          />
        </dl>
        <SafeDetails record={settings} />
      </TraceCard>

      <TraceCard title="Latency Breakdown">
        <table className="admin-table compact-table">
          <tbody>
            {LATENCY_KEYS.map((key) => (
              <tr key={key}>
                <th>{key}</th>
                <td>{formatLatency(latency[key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TraceCard>

      <TraceCard title="Retrieval Score Summary">
        <KeyValueTable record={summary} />
      </TraceCard>
    </section>
  );
}

function ContextBudgetPanel({ trace }: { trace: ContextBudgetTrace | null }) {
  if (!trace) {
    return (
      <section className="admin-section">
        <h2>Context Budget</h2>
        <EmptyState title="No context budget trace">
          Context budget data is recorded for RAG ask runs after PR-40.
        </EmptyState>
      </section>
    );
  }

  return (
    <section className="admin-section">
      <h2>Context Budget</h2>
      <dl className="detail-grid">
        <Detail label="enabled" value={formatUnknownValue(trace.enabled)} />
        <Detail label="max_context_tokens" value={trace.budget.max_context_tokens} />
        <Detail label="estimated_context_tokens" value={trace.usage.estimated_context_tokens} />
        <Detail label="remaining_context_tokens" value={trace.usage.remaining_context_tokens} />
        <Detail label="selected_count" value={trace.items.selected_count} />
        <Detail label="dropped_count" value={trace.items.dropped_count} />
        <Detail label="citation_candidate_count" value={trace.items.citation_candidate_count} />
        <Detail label="source_count" value={trace.items.source_count} />
        <Detail label="budget_exhausted" value={formatUnknownValue(trace.usage.budget_exhausted)} />
      </dl>
      <div className="retrieval-debug-grid">
        <TraceCard title="Drop Reasons">
          <KeyValueTable record={trace.drop_reasons} />
        </TraceCard>
        <TraceCard title="Source Breakdown">
          <table className="admin-table compact-table">
            <thead>
              <tr>
                <th>source</th>
                <th>candidates</th>
                <th>selected</th>
                <th>dropped</th>
                <th>tokens</th>
              </tr>
            </thead>
            <tbody>
              {trace.sources.by_source.map((source) => (
                <tr key={source.source_group_key}>
                  <td>{formatDebugText(source.source_label ?? source.source_group_key, 80)}</td>
                  <td>{source.candidate_count}</td>
                  <td>{source.selected_count}</td>
                  <td>{source.dropped_count}</td>
                  <td>{source.estimated_tokens}</td>
                </tr>
              ))}
              {trace.sources.by_source.length === 0 ? (
                <tr>
                  <td colSpan={5}>No sources.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </TraceCard>
      </div>
      <ContextBudgetItemTable title="Selected Context Items" items={trace.selected_item_refs} />
      <ContextBudgetItemTable title="Dropped Context Items" items={trace.dropped_item_refs} />
    </section>
  );
}

function ContextBudgetItemTable({
  items,
  title
}: {
  items: ContextBudgetItemRef[];
  title: string;
}) {
  return (
    <section>
      <h3>{title}</h3>
      <table className="admin-table compact-table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Chunk</th>
            <th>Source</th>
            <th>Rank</th>
            <th>Chars</th>
            <th>Tokens</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${title}-${item.retrieval_run_item_id}`}>
              <td>{item.retrieval_run_item_id}</td>
              <td>{item.document_chunk_id}</td>
              <td>{formatDebugText(item.source_label ?? null, 80)}</td>
              <td>{formatUnknownValue(item.rank ?? item.rerank_order)}</td>
              <td>{item.char_count}</td>
              <td>{item.estimated_tokens}</td>
              <td>{formatUnknownValue(item.reason ?? item.drop_reason)}</td>
            </tr>
          ))}
          {items.length === 0 ? (
            <tr>
              <td colSpan={7}>No items.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

function EvidencePackPanel({ trace }: { trace: EvidencePackTrace | null }) {
  if (!trace) {
    return (
      <section className="admin-section">
        <h2>Evidence Pack</h2>
        <EmptyState title="No evidence pack trace">
          Evidence Pack data is recorded for RAG ask runs after PR-41.
        </EmptyState>
      </section>
    );
  }

  return (
    <section className="admin-section">
      <h2>Evidence Pack</h2>
      <dl className="detail-grid">
        <Detail label="enabled" value={formatUnknownValue(trace.enabled)} />
        <Detail label="method" value={formatUnknownValue(trace.method)} />
        <Detail label="input_items" value={trace.input.selected_context_items} />
        <Detail label="evidence_items" value={trace.output.evidence_item_count} />
        <Detail label="evidence_groups" value={trace.output.evidence_group_count} />
        <Detail label="compression_ratio" value={formatRatio(trace.output.compression_ratio)} />
        <Detail label="input_chars" value={trace.input.input_char_count} />
        <Detail label="output_chars" value={trace.output.output_char_count} />
        <Detail label="citation_candidate_count" value={trace.output.citation_candidate_count} />
        <Detail
          label="max_items_per_source"
          value={formatUnknownValue(trace.policy.max_items_per_source)}
        />
      </dl>
      <div className="retrieval-debug-grid">
        <TraceCard title="Compression Drops">
          <KeyValueTable record={trace.drops} />
        </TraceCard>
        <TraceCard title="Evidence Groups">
          <table className="admin-table compact-table">
            <thead>
              <tr>
                <th>source</th>
                <th>items</th>
                <th>tokens</th>
                <th>top_score</th>
              </tr>
            </thead>
            <tbody>
              {trace.evidence_groups.map((group) => (
                <tr key={group.source_group_key}>
                  <td>{formatDebugText(group.source_label ?? group.source_group_key, 80)}</td>
                  <td>{group.item_count}</td>
                  <td>{group.estimated_tokens}</td>
                  <td>{formatScore(group.top_score)}</td>
                </tr>
              ))}
              {trace.evidence_groups.length === 0 ? (
                <tr>
                  <td colSpan={4}>No evidence groups.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </TraceCard>
      </div>
      <EvidenceItemTable items={trace.evidence_item_refs} />
      <DroppedEvidenceTable items={trace.dropped_item_refs} />
    </section>
  );
}

function EvidenceItemTable({ items }: { items: EvidenceItemRef[] }) {
  return (
    <section>
      <h3>Evidence Items</h3>
      <table className="admin-table compact-table">
        <thead>
          <tr>
            <th>Evidence</th>
            <th>Item</th>
            <th>Chunk</th>
            <th>Citation</th>
            <th>Source</th>
            <th>Chars</th>
            <th>Tokens</th>
            <th>Method</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.evidence_item_id}>
              <td>{formatDebugText(item.evidence_item_id, 40)}</td>
              <td>{item.retrieval_run_item_id}</td>
              <td>{item.document_chunk_id}</td>
              <td>{item.local_citation_id}</td>
              <td>{formatDebugText(item.source_label ?? null, 80)}</td>
              <td>{`${item.output_char_count}/${item.original_char_count}`}</td>
              <td>{item.estimated_tokens}</td>
              <td>{formatUnknownValue(item.compression_reason ?? item.compression_method)}</td>
            </tr>
          ))}
          {items.length === 0 ? (
            <tr>
              <td colSpan={8}>No evidence items.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

function DroppedEvidenceTable({ items }: { items: DroppedEvidenceRef[] }) {
  return (
    <section>
      <h3>Dropped Evidence</h3>
      <table className="admin-table compact-table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Chunk</th>
            <th>Source</th>
            <th>Rank</th>
            <th>Chars</th>
            <th>Tokens</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.retrieval_run_item_id}-${item.drop_reason}`}>
              <td>{item.retrieval_run_item_id}</td>
              <td>{item.document_chunk_id}</td>
              <td>{formatDebugText(item.source_label ?? null, 80)}</td>
              <td>{formatUnknownValue(item.rank ?? item.rerank_order)}</td>
              <td>{item.original_char_count}</td>
              <td>{item.estimated_tokens}</td>
              <td>{formatUnknownValue(item.drop_reason)}</td>
            </tr>
          ))}
          {items.length === 0 ? (
            <tr>
              <td colSpan={7}>No dropped evidence.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

function ToolResultCompressionPanel({ trace }: { trace: ToolResultCompressionTrace | null }) {
  if (!trace) {
    return (
      <section className="admin-section">
        <h2>Tool Result Compression</h2>
        <EmptyState title="No tool result compression trace">
          Tool result compression data is recorded for Auto / LLM tool orchestrator ask runs after PR-42.
        </EmptyState>
      </section>
    );
  }

  return (
    <section className="admin-section">
      <h2>Tool Result Compression</h2>
      <dl className="detail-grid">
        <Detail label="enabled" value={formatUnknownValue(trace.enabled)} />
        <Detail label="tool_call_count" value={trace.summary.tool_call_count} />
        <Detail label="search_tool_call_count" value={trace.summary.search_tool_call_count} />
        <Detail label="original_items" value={trace.summary.original_item_count} />
        <Detail label="output_items" value={trace.summary.output_item_count} />
        <Detail label="dropped_items" value={trace.summary.dropped_item_count} />
        <Detail label="compression_ratio" value={formatRatio(trace.summary.compression_ratio)} />
        <Detail label="tokens_before" value={trace.summary.estimated_tokens_before} />
        <Detail label="tokens_after" value={trace.summary.estimated_tokens_after} />
        <Detail label="max_items_per_tool" value={trace.budget.max_items_per_tool} />
        <Detail
          label="max_total_tool_result_tokens"
          value={trace.budget.max_total_tool_result_tokens}
        />
        <Detail label="budget_exhausted" value={formatUnknownValue(trace.summary.budget_exhausted)} />
        <Detail label="oversized_rejected" value={trace.summary.oversized_rejected_count} />
      </dl>
      <div className="retrieval-debug-grid">
        <TraceCard title="Drop Reasons">
          <KeyValueTable record={trace.drop_reasons} />
        </TraceCard>
        <TraceCard title="By Tool">
          <table className="admin-table compact-table">
            <thead>
              <tr>
                <th>tool_call</th>
                <th>tool</th>
                <th>status</th>
                <th>items</th>
                <th>tokens</th>
                <th>ratio</th>
                <th>flags</th>
              </tr>
            </thead>
            <tbody>
              {trace.by_tool.map((tool) => (
                <tr key={tool.tool_call_id}>
                  <td>{formatDebugText(tool.tool_call_id, 40)}</td>
                  <td>{formatDebugText(tool.tool_name, 80)}</td>
                  <td>{formatUnknownValue(tool.status)}</td>
                  <td>{`${tool.output_item_count}/${tool.original_item_count}`}</td>
                  <td>{`${tool.estimated_tokens_after}/${tool.estimated_tokens_before}`}</td>
                  <td>{formatRatio(tool.compression_ratio)}</td>
                  <td>
                    {formatUnknownValue(
                      [
                        tool.budget_exhausted ? "budget_exhausted" : null,
                        tool.repeated_result ? "repeated_result" : null,
                        tool.oversized_rejected ? "oversized_rejected" : null,
                        tool.error_code ?? null
                      ].filter(Boolean)
                    )}
                  </td>
                </tr>
              ))}
              {trace.by_tool.length === 0 ? (
                <tr>
                  <td colSpan={7}>No tool calls.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </TraceCard>
      </div>
      <ToolResultItemTable items={trace.item_refs} />
    </section>
  );
}

function ToolResultItemTable({ items }: { items: ToolResultItemRef[] }) {
  return (
    <section>
      <h3>Tool Result Items</h3>
      <table className="admin-table compact-table">
        <thead>
          <tr>
            <th>tool_call</th>
            <th>Item</th>
            <th>Chunk</th>
            <th>Source</th>
            <th>Rank</th>
            <th>Chars</th>
            <th>Tokens</th>
            <th>Method</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.tool_call_id}-${item.document_chunk_id}`}>
              <td>{formatDebugText(item.tool_call_id, 40)}</td>
              <td>{formatUnknownValue(item.retrieval_run_item_id)}</td>
              <td>{item.document_chunk_id}</td>
              <td>{formatDebugText(item.source_label ?? null, 80)}</td>
              <td>{formatUnknownValue(item.rank)}</td>
              <td>{`${item.snippet_char_count}/${item.original_char_count}`}</td>
              <td>{item.estimated_tokens}</td>
              <td>{formatUnknownValue(item.compression_method)}</td>
            </tr>
          ))}
          {items.length === 0 ? (
            <tr>
              <td colSpan={8}>No tool result items.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

function ScoreBreakdownTable({ items }: { items: DisplayItem[] }) {
  return (
    <section className="admin-section">
      <h2>Score Breakdown</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Chunk</th>
            <th>Source</th>
            <th>dense</th>
            <th>sparse</th>
            <th>fusion</th>
            <th>rerank</th>
            <th>selected</th>
          </tr>
        </thead>
        <tbody>
          {items.map(({ detailItem, key, searchItem }) => {
            const breakdown = safeRecord(detailItem?.score_breakdown_json);
            return (
              <tr key={key}>
                <td>{formatUnknownValue(breakdown.final_rank ?? detailItem?.rank_order ?? searchItem?.rank_order)}</td>
                <td>{detailItem?.document_chunk_id ?? searchItem?.document_chunk_id ?? "N/A"}</td>
                <td>{formatDebugText(searchItem?.source_label ?? detailItem?.source_label ?? null, 48)}</td>
                <td>{formatScore(breakdown.dense_score)}</td>
                <td>{formatScore(breakdown.sparse_score)}</td>
                <td>{formatScore(breakdown.fused_score ?? breakdown.fusion_score)}</td>
                <td>{formatScore(breakdown.rerank_score ?? searchItem?.rerank_score)}</td>
                <td>{formatUnknownValue(breakdown.selected_flag ?? detailItem?.selected_flag ?? searchItem?.selected_flag)}</td>
              </tr>
            );
          })}
          {items.length === 0 ? (
            <tr>
              <td colSpan={8}>No retrieval items.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

function RetrievalRunItemsTable({ items }: { items: DisplayItem[] }) {
  return (
    <section className="admin-section">
      <h2>Retrieval Run Items</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Chunk</th>
            <th>Page</th>
            <th>retrieval_source</th>
            <th>rank_order</th>
            <th>rerank_order</th>
            <th>retrieval_score</th>
            <th>snippet</th>
          </tr>
        </thead>
        <tbody>
          {items.map(({ detailItem, key, searchItem }) => (
            <tr key={key}>
              <td>{detailItem?.retrieval_run_item_id ?? searchItem?.retrieval_run_item_id ?? "N/A"}</td>
              <td>{detailItem?.document_chunk_id ?? searchItem?.document_chunk_id ?? "N/A"}</td>
              <td>{formatPage(searchItem?.page_from ?? detailItem?.page_from, searchItem?.page_to ?? detailItem?.page_to)}</td>
              <td>{formatUnknownValue(detailItem?.retrieval_source ?? "N/A")}</td>
              <td>{detailItem?.rank_order ?? searchItem?.rank_order ?? "N/A"}</td>
              <td>{detailItem?.rerank_order ?? searchItem?.rerank_order ?? "N/A"}</td>
              <td>{formatScore(detailItem?.retrieval_score ?? searchItem?.retrieval_score)}</td>
              <td>{formatDebugText(searchItem?.snippet ?? null, 160)}</td>
            </tr>
          ))}
          {items.length === 0 ? (
            <tr>
              <td colSpan={8}>No retrieval items.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </section>
  );
}

function EvaluationStrategySummaryPanel({ metrics }: { metrics: StrategyComparisonMetric[] }) {
  const visibleMetrics = metrics.filter((metric) => EVALUATION_METRICS.includes(metric.metric_name));
  return (
    <section className="admin-section">
      <h2>Evaluation Strategy Summary</h2>
      {visibleMetrics.length ? (
        <table className="admin-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Metric</th>
              <th>Average</th>
              <th>p95</th>
              <th>Count</th>
              <th>Failed</th>
            </tr>
          </thead>
          <tbody>
            {visibleMetrics.map((metric) => (
              <tr key={`${metric.strategy_type}-${metric.metric_name}`}>
                <td>{metric.strategy_type}</td>
                <td>{metric.metric_name}</td>
                <td>{formatScore(metric.average)}</td>
                <td>{formatScore(metric.p95)}</td>
                <td>{metric.count}</td>
                <td>{metric.failed_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No strategy summary">
          Strategy evaluation summary will be available after an evaluation run.
        </EmptyState>
      )}
    </section>
  );
}

function TraceCard({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="trace-card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Detail({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function SafeDetails({ record }: { record: Record<string, unknown> }) {
  if (!Object.keys(record).length) {
    return <p className="muted">No safe trace fields.</p>;
  }
  return (
    <details className="safe-json-details">
      <summary>Safe fields</summary>
      <KeyValueTable record={record} />
    </details>
  );
}

function KeyValueTable({ record }: { record: Record<string, unknown> }) {
  const entries = Object.entries(safeRecord(record));
  return (
    <table className="admin-table compact-table">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th>{key}</th>
            <td>{formatUnknownValue(value)}</td>
          </tr>
        ))}
        {entries.length === 0 ? (
          <tr>
            <td>No fields.</td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

function NestedList({ items, title }: { items: unknown[]; title: string }) {
  if (!items.length) {
    return <p className="muted">{title}: N/A</p>;
  }
  return (
    <div>
      <h3>{title}</h3>
      <ul className="compact-list">
        {items.map((item, index) => (
          <li key={index}>{formatUnknownValue(item)}</li>
        ))}
      </ul>
    </div>
  );
}

function buildDisplayItems(
  searchItems: RagSearchDebugItem[],
  detailItems: RetrievalRunDebugItem[]
): DisplayItem[] {
  if (!searchItems.length) {
    return detailItems.map((detailItem) => ({
      key: String(detailItem.retrieval_run_item_id),
      detailItem,
      searchItem: null
    }));
  }
  return searchItems.map((searchItem) => {
    const detailItem =
      detailItems.find((item) => item.retrieval_run_item_id === searchItem.retrieval_run_item_id) ??
      detailItems.find((item) => item.document_chunk_id === searchItem.document_chunk_id) ??
      null;
    return {
      key: String(searchItem.retrieval_run_item_id),
      detailItem,
      searchItem
    };
  });
}

function latestStrategyMetrics(runs: Array<{ strategy_comparison: StrategyComparisonMetric[] }>) {
  return runs.find((run) => run.strategy_comparison.length > 0)?.strategy_comparison ?? [];
}

function traceField(
  decision: Record<string, unknown>,
  summary: Record<string, unknown>,
  key: string,
  fallback?: unknown,
  preferSummary = false
) {
  const summaryValue = summary[key];
  const decisionValue = decision[key];
  if (preferSummary && summaryValue !== null && summaryValue !== undefined) {
    return summaryValue;
  }
  if (decisionValue !== null && decisionValue !== undefined) {
    return decisionValue;
  }
  if (summaryValue !== null && summaryValue !== undefined) {
    return summaryValue;
  }
  return fallback;
}

function traceRetrievalCallCount(
  decision: Record<string, unknown>,
  summary: Record<string, unknown>,
  preferSummary = false
) {
  const retrievalCallCount = traceField(decision, summary, "retrieval_call_count", undefined, preferSummary);
  if (retrievalCallCount !== null && retrievalCallCount !== undefined) {
    return retrievalCallCount;
  }
  return traceField(decision, summary, "search_call_count", "N/A", preferSummary);
}

function clampNumber(value: number, min: number, max: number, fallback: number) {
  if (!Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

function formatScore(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "N/A";
  }
  return value.toFixed(3);
}

function formatRatio(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "N/A";
  }
  return value.toFixed(3);
}

function formatScoreWithNote(value: unknown, note?: string) {
  const formatted = formatScore(value);
  return formatted === "N/A" && note ? `N/A (${note})` : formatted;
}

function formatLatency(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return "N/A";
  }
  return `${value} ms`;
}

function formatPage(pageFrom: number | null | undefined, pageTo: number | null | undefined) {
  if (pageFrom === null || pageFrom === undefined) {
    return "N/A";
  }
  return pageTo && pageTo !== pageFrom ? `${pageFrom}-${pageTo}` : String(pageFrom);
}

function formatDebugText(value: string | null | undefined, maxLength: number) {
  if (!value) {
    return "N/A";
  }
  return truncateText(redactString(value, maxLength), maxLength);
}

function shortHash(value: unknown) {
  if (typeof value !== "string" || value.length < 12) {
    return "N/A";
  }
  return `${value.slice(0, 12)}...`;
}

function recordFrom(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? safeRecord(value as Record<string, unknown>)
    : {};
}

function arrayFrom(value: unknown) {
  return Array.isArray(value) ? value : [];
}
