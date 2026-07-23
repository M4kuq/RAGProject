import { FormEvent, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  HelpTooltip,
  MetricHelp,
  orderedMetricEntries
} from "../../../components/admin/MetricHelp";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import {
  useCreateEvaluationRun,
  useEvaluationDatasets,
  useEvaluationRuns
} from "../../../features/evaluations/evaluationHooks";
import { buildEvaluationGenerationProviders } from "../../../features/evaluations/generationProviders";
import type {
  EvaluationCacheMode,
  EvaluationGenerationProvider,
  EvaluationRunnableStrategy
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, truncateText } from "../../../lib/format";
import {
  isNvidiaApiEnabled,
  NVIDIA_EXTERNAL_DATA_WARNING,
  nvidiaModelIds,
  NVIDIA_RECOMMENDED_MODEL_ID
} from "../../../lib/modelCatalog";

const PAGE_SIZE = 20;
const ANSWER_GENERATION_STRATEGIES: EvaluationRunnableStrategy[] = [
  "llm_tool_orchestrator",
  "langchain_agentic",
  "langgraph_agentic"
];

export function EvaluationListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const nvidiaApiEnabled = isNvidiaApiEnabled();
  const nvidiaModels = nvidiaModelIds();
  const generationProviders: EvaluationGenerationProvider[] =
    buildEvaluationGenerationProviders(nvidiaApiEnabled);

  const [datasetName, setDatasetName] = useState("phase1_smoke");
  const [evaluationDatasetId, setEvaluationDatasetId] = useState<number | null>(null);
  const [caseLimit, setCaseLimit] = useState(10);
  const [strategies, setStrategies] = useState<EvaluationRunnableStrategy[]>(["dense"]);
  const [cacheModes, setCacheModes] = useState<EvaluationCacheMode[]>(["default"]);
  const [generationProvider, setGenerationProvider] = useState<EvaluationGenerationProvider | "">(
    ""
  );
  const [generationModel, setGenerationModel] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [selectedRunIds, setSelectedRunIds] = useState<number[]>([]);
  const navigate = useNavigate();
  const params = useMemo(
    () => ({
      page: Number(searchParams.get("page") ?? 1),
      page_size: PAGE_SIZE
    }),
    [searchParams]
  );
  const runs = useEvaluationRuns(params);
  const datasets = useEvaluationDatasets({ page: 1, page_size: 50 });
  const createRun = useCreateEvaluationRun();
  const trimmedGenerationModel = generationModel.trim();
  const generationSelectionRequested = Boolean(generationProvider || trimmedGenerationModel);
  const hasAnswerGenerationStrategy = strategies.some((strategy) =>
    ANSWER_GENERATION_STRATEGIES.includes(strategy)
  );
  const providerWithoutModel = Boolean(generationProvider && !trimmedGenerationModel);
  const generationSelectionBlocked =
    generationSelectionRequested && !hasAnswerGenerationStrategy;
  const generationGuardMessage = providerWithoutModel
    ? "逕滓・ provider 繧呈欠螳壹☆繧句ｴ蜷医・逕滓・ model 繧ょ・蜉帙＠縺ｦ縺上□縺輔＞縲・
    : generationSelectionBlocked
      ? "provider/model 豈碑ｼ・↓縺ｯ llm_tool_orchestrator縲〕angchain_agentic縲〕anggraph_agentic 縺ｮ縺・★繧後°繧帝∈謚槭＠縺ｦ縺上□縺輔＞縲・
      : null;

  function updatePage(page: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(page));
    setSearchParams(next);
  }

  function changeGenerationProvider(
    provider: EvaluationGenerationProvider | ""
  ) {
    setGenerationProvider(provider);
    if (
      provider === "nvidia" &&
      (!generationModel.trim() || generationModel === "meta/llama-3.3-70b-instruct")
    ) {
      setGenerationModel(NVIDIA_RECOMMENDED_MODEL_ID);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (generationGuardMessage) {
      return;
    }
    const safeCaseLimit = Number.isFinite(caseLimit) ? Math.min(50, Math.max(1, caseLimit)) : 10;
    const selectedDataset = datasets.data?.items.find(
      (dataset) => dataset.evaluation_dataset_id === evaluationDatasetId
    );
    const result = await createRun.mutateAsync({
      dataset_name: selectedDataset?.dataset_name ?? (datasetName.trim() || "phase1_smoke"),
      evaluation_dataset_id: evaluationDatasetId,
      case_limit: safeCaseLimit,
      strategy_type: strategies[0] ?? "dense",
      strategies,
      cache_modes: cacheModes,
      generation_provider: generationProvider || undefined,
      generation_model: trimmedGenerationModel || undefined,
      trigger_type: "manual"
    });
    setMessage(`隧穂ｾ｡ run #${result.evaluation_run_id} 繧偵ず繝ｧ繝・#${result.job_id} 縺ｨ縺励※逋ｻ骭ｲ縺励∪縺励◆縲Ａ);
  }

  function toggleSelectedRun(evaluationRunId: number, checked: boolean) {
    setSelectedRunIds((current) => {
      if (!checked) {
        return current.filter((runId) => runId !== evaluationRunId);
      }
      if (current.includes(evaluationRunId) || current.length >= 2) {
        return current;
      }
      return [...current, evaluationRunId];
    });
  }

  function openComparison() {
    if (selectedRunIds.length !== 2) {
      return;
    }
    const [base, candidate] = selectedRunIds;
    navigate(`/admin/evaluations/compare?base=${base}&candidate=${candidate}`);
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>隧穂ｾ｡</h1>
          <p className="muted">
            隧穂ｾ｡ dataset 繧・fixture 繧剃ｽｿ縺｣縺ｦ讀懃ｴ｢蜩∬ｳｪ繧堤｢ｺ隱阪＠縲∝ｮ牙・縺ｪ metric summary 繧堤｢ｺ隱阪＠縺ｾ縺吶・          </p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {generationGuardMessage ? (
        <InlineAlert tone="error">{generationGuardMessage}</InlineAlert>
      ) : null}
      {generationProvider === "nvidia" ? (
        <InlineAlert>{NVIDIA_EXTERNAL_DATA_WARNING}</InlineAlert>
      ) : null}
      {createRun.error ? <InlineAlert tone="error">{createRun.error.message}</InlineAlert> : null}
      <form className="filter-bar" onSubmit={submit}>
        <label>
          fixture 蜷・          <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
        </label>
        <label>
          dataset
          <select
            value={evaluationDatasetId ?? ""}
            onChange={(event) =>
              setEvaluationDatasetId(event.target.value ? Number(event.target.value) : null)
            }
          >
            <option value="">fixture 縺ｮ縺ｿ</option>
            {datasets.data?.items.map((dataset) => (
              <option key={dataset.evaluation_dataset_id} value={dataset.evaluation_dataset_id}>
                {dataset.dataset_name}
              </option>
            ))}
          </select>
        </label>
        <div className="field-group">
          strategy
          <span className="inline-options">
            {(
              [
                "dense",
                "sparse",
                "hybrid",
                "graph_postgres",
                "graph_neo4j",
                "agentic_router",
                "llm_tool_orchestrator",
                "langchain_agentic",
                "langgraph_agentic"
              ] as EvaluationRunnableStrategy[]
            ).map((strategy) => (
              <label key={strategy}>
                <input
                  type="checkbox"
                  checked={strategies.includes(strategy)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...strategies, strategy]
                      : strategies.filter((item) => item !== strategy);
                    setStrategies(next.length ? next : ["dense"]);
                  }}
                />
                {strategy}
              </label>
            ))}
          </span>
        </div>
        <div className="field-group">
          cache mode
          <span className="inline-options">
            {(["default", "disabled", "cold", "warm"] as EvaluationCacheMode[]).map((mode) => (
              <label key={mode}>
                <input
                  type="checkbox"
                  checked={cacheModes.includes(mode)}
                  onChange={(event) => {
                    const next = nextCacheModes(cacheModes, mode, event.target.checked);
                    setCacheModes(next.length ? next : ["default"]);
                  }}
                />
                {mode}
              </label>
            ))}
          </span>
        </div>
        <label>
          <span className="metric-heading">
            逕滓・ provider
            <HelpTooltip
              description="譛ｪ謖・ｮ壹↑繧峨し繝ｼ繝舌・譌｢螳・provider 繧剃ｽｿ縺・∪縺吶・
              direction="provider 繧呈欠螳壹☆繧句ｴ蜷医・ model 繧ょ・蜉帙＠縺ｦ縺上□縺輔＞縲・
              title="逕滓・ provider"
            />
          </span>
          <select
            aria-label="逕滓・ provider"
            value={generationProvider}
            onChange={(event) =>
              changeGenerationProvider(event.target.value as EvaluationGenerationProvider | "")
            }
          >
            <option value="">繧ｷ繧ｹ繝・Β譌｢螳・/option>
            {generationProviders.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="metric-heading">
            逕滓・ model
            <HelpTooltip
              description="provider/model 縺ｨ繧よ悴謖・ｮ壹↑繧峨し繝ｼ繝先里螳壹Δ繝・Ν繧剃ｽｿ縺・∪縺吶・
              direction="API key 繧・token 縺ｧ縺ｯ縺ｪ縺・model 蜷阪□縺代ｒ蜈･蜉帙＠縺ｦ縺上□縺輔＞縲・
              title="逕滓・ model"
            />
          </span>
          <input
            aria-label="逕滓・ model"
            list={generationProvider === "nvidia" ? "nvidia-generation-models" : undefined}
            maxLength={128}
            placeholder="萓・ gpt-4.1-mini"
            value={generationModel}
            onChange={(event) => setGenerationModel(event.target.value)}
          />
          {nvidiaApiEnabled ? (
            <datalist id="nvidia-generation-models">
              {nvidiaModels.map((modelId) => (
                <option key={modelId} value={modelId} />
              ))}
            </datalist>
          ) : null}
        </label>
        <label>
          繧ｱ繝ｼ繧ｹ荳企剞
          <input
            type="number"
            min={1}
            max={50}
            value={caseLimit}
            onChange={(event) => setCaseLimit(Number(event.target.value))}
          />
        </label>
        <button
          type="submit"
          disabled={createRun.isPending || providerWithoutModel || generationSelectionBlocked}
        >
          隧穂ｾ｡繧貞ｮ溯｡・        </button>
        <button type="button" onClick={() => void runs.refetch()}>
          譖ｴ譁ｰ
        </button>
      </form>
      {runs.isLoading ? <LoadingState label="隧穂ｾ｡ run 繧定ｪｭ縺ｿ霎ｼ繧薙〒縺・∪縺・.." /> : null}
      {runs.error ? <ErrorState error={runs.error} /> : null}
      {runs.data?.items.length === 0 ? (
        <EmptyState title="隧穂ｾ｡ run 縺後≠繧翫∪縺帙ｓ">荳翫・繝輔か繝ｼ繝縺九ｉ隧穂ｾ｡繧貞ｮ溯｡後☆繧九→縲∫ｵ先棡縺ｨ metric 縺後％縺薙↓陦ｨ遉ｺ縺輔ｌ縺ｾ縺吶・/EmptyState>
      ) : null}
      {runs.data && runs.data.items.length > 0 ? (
        <>
          <div className="comparison-toolbar">
            <span className="muted">
              豈碑ｼ・☆繧・run 繧・2 莉ｶ驕ｸ謚槭＠縺ｦ縺上□縺輔＞縲る∈謚樣・↓ base / candidate 縺ｨ縺励※謇ｱ縺・∪縺吶・            </span>
            <button type="button" disabled={selectedRunIds.length !== 2} onClick={openComparison}>
              豈碑ｼ・            </button>
            <button
              type="button"
              disabled={selectedRunIds.length === 0}
              onClick={() => setSelectedRunIds([])}
            >
              驕ｸ謚櫁ｧ｣髯､
            </button>
          </div>
          <table className="admin-table">
            <thead>
              <tr>
                <th>隧穂ｾ｡ run</th>
                <th>豈碑ｼ・/th>
                <th>dataset</th>
                <th>strategy</th>
                <th>迥ｶ諷・/th>
                <th>繧ｱ繝ｼ繧ｹ</th>
                <th>
                  <span className="metric-heading">
                    Metrics
                    <MetricHelp metricName="metric_summary" />
                  </span>
                </th>
                <th>
                  <span className="metric-heading">
                    謗ｨ螳壹さ繧ｹ繝・                    <HelpTooltip
                      description="隧穂ｾ｡ run 縺ｮ謌仙粥繧ｱ繝ｼ繧ｹ縺ｧ險倬鹸縺輔ｌ縺・LLM 逕滓・繧ｳ繧ｹ繝医・讎らｮ怜粋險医〒縺吶・
                      direction="usage 繧・pricing 縺悟叙蠕励〒縺阪↑縺・ｴ蜷医・ - 縺ｫ縺ｪ繧翫∪縺吶・
                      title="謗ｨ螳壹さ繧ｹ繝茨ｼ域ｦらｮ暦ｼ・
                    />
                  </span>
                </th>
                <th>繧ｸ繝ｧ繝・/th>
                <th>髢句ｧ区律譎・/th>
                <th>邨ゆｺ・律譎・/th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((run) => (
                <tr key={run.evaluation_run_id}>
                  <td>
                    <Link to={`/admin/evaluations/${run.evaluation_run_id}`}>#{run.evaluation_run_id}</Link>
                  </td>
                  <td>
                    <input
                      aria-label={`豈碑ｼ・ｯｾ雎｡ run #${run.evaluation_run_id} 繧帝∈謚杼}
                      checked={selectedRunIds.includes(run.evaluation_run_id)}
                      disabled={
                        !selectedRunIds.includes(run.evaluation_run_id) &&
                        selectedRunIds.length >= 2
                      }
                      onChange={(event) =>
                        toggleSelectedRun(run.evaluation_run_id, event.target.checked)
                      }
                      type="checkbox"
                    />
                  </td>
                  <td>{truncateText(run.dataset_name, 32)}</td>
                  <td>{run.strategies.length ? run.strategies.join(", ") : run.strategy_type}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td>
                    謌仙粥 {run.succeeded_count}/{run.case_count}
                    {run.failed_count ? ` / 螟ｱ謨・${run.failed_count}` : ""}
                  </td>
                  <td>{formatMetricSummary(run.metric_summary)}</td>
                  <td>{formatCost(run.total_estimated_cost_usd)}</td>
                  <td>{run.job_id ? <Link to={`/admin/jobs/${run.job_id}`}>#{run.job_id}</Link> : "-"}</td>
                  <td>{formatDate(run.started_at)}</td>
                  <td>{formatDate(run.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination meta={runs.data.pagination} onPageChange={updatePage} />
        </>
      ) : null}

      <section className="admin-section">
        <h2>Datasets</h2>
        {datasets.isLoading ? <LoadingState label="dataset 繧定ｪｭ縺ｿ霎ｼ繧薙〒縺・∪縺・.." /> : null}
        {datasets.error ? <ErrorState error={datasets.error} /> : null}
        {datasets.data && datasets.data.items.length > 0 ? (
          <table className="admin-table">
            <thead>
              <tr>
                <th>dataset</th>
                <th>迥ｶ諷・/th>
                <th>source</th>
                <th>繧ｱ繝ｼ繧ｹ</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {datasets.data.items.map((dataset) => (
                <tr key={dataset.evaluation_dataset_id}>
                  <td>
                    <Link to={`/admin/evaluations/datasets/${dataset.evaluation_dataset_id}`}>
                      {truncateText(dataset.dataset_name, 40)}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={dataset.status} />
                  </td>
                  <td>{dataset.source_type}</td>
                  <td>{dataset.case_count}</td>
                  <td>{dataset.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </main>
  );
}

function formatMetricSummary(summary: Record<string, number>) {
  const entries = orderedMetricEntries(Object.entries(summary));
  if (!entries.length) {
    return "-";
  }
  return (
    <span className="metric-detail-list">
      {entries.map(([name, value]) => (
        <span className="metric-detail-item" key={name}>
          <span>{name}: {value.toFixed(2)}</span>
          <MetricHelp metricName={name} />
        </span>
      ))}
    </span>
  );
}

function formatCost(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : `$${value.toFixed(6)}`;
}

function nextCacheModes(
  current: EvaluationCacheMode[],
  mode: EvaluationCacheMode,
  checked: boolean
): EvaluationCacheMode[] {
  if (checked && mode === "default") {
    return ["default"];
  }
  if (checked) {
    return [...current.filter((item) => item !== "default"), mode];
  }
  return current.filter((item) => item !== mode);
}

