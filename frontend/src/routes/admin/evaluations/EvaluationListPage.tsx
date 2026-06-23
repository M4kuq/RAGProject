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
import type {
  EvaluationCacheMode,
  EvaluationGenerationProvider,
  EvaluationRunnableStrategy
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;
const GENERATION_PROVIDERS: EvaluationGenerationProvider[] = [
  "fake",
  "ollama",
  "lmstudio",
  "openai",
  "anthropic",
  "gemini"
];
const ANSWER_GENERATION_STRATEGIES: EvaluationRunnableStrategy[] = [
  "llm_tool_orchestrator",
  "langchain_agentic",
  "langgraph_agentic"
];

export function EvaluationListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
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
    ? "生成 provider を指定する場合は生成 model も入力してください。"
    : generationSelectionBlocked
      ? "provider/model 比較には llm_tool_orchestrator、langchain_agentic、langgraph_agentic のいずれかを選択してください。"
      : null;

  function updatePage(page: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(page));
    setSearchParams(next);
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
    setMessage(`評価 run #${result.evaluation_run_id} をジョブ #${result.job_id} として登録しました。`);
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
          <h1>評価</h1>
          <p className="muted">
            評価 dataset や fixture を使って検索品質を確認し、安全な metric summary を確認します。
          </p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {generationGuardMessage ? (
        <InlineAlert tone="error">{generationGuardMessage}</InlineAlert>
      ) : null}
      {createRun.error ? <InlineAlert tone="error">{createRun.error.message}</InlineAlert> : null}
      <form className="filter-bar" onSubmit={submit}>
        <label>
          fixture 名
          <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
        </label>
        <label>
          dataset
          <select
            value={evaluationDatasetId ?? ""}
            onChange={(event) =>
              setEvaluationDatasetId(event.target.value ? Number(event.target.value) : null)
            }
          >
            <option value="">fixture のみ</option>
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
            生成 provider
            <HelpTooltip
              description="未指定ならサーバの既定 provider を使います。"
              direction="provider を指定する場合は model も入力してください。"
              title="生成 provider"
            />
          </span>
          <select
            aria-label="生成 provider"
            value={generationProvider}
            onChange={(event) =>
              setGenerationProvider(event.target.value as EvaluationGenerationProvider | "")
            }
          >
            <option value="">システム既定</option>
            {GENERATION_PROVIDERS.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="metric-heading">
            生成 model
            <HelpTooltip
              description="provider/model とも未指定ならサーバ既定モデルを使います。"
              direction="API key や token ではなく model 名だけを入力してください。"
              title="生成 model"
            />
          </span>
          <input
            aria-label="生成 model"
            maxLength={128}
            placeholder="例: gpt-4.1-mini"
            value={generationModel}
            onChange={(event) => setGenerationModel(event.target.value)}
          />
        </label>
        <label>
          ケース上限
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
          評価を実行
        </button>
        <button type="button" onClick={() => void runs.refetch()}>
          更新
        </button>
      </form>
      {runs.isLoading ? <LoadingState label="評価 run を読み込んでいます..." /> : null}
      {runs.error ? <ErrorState error={runs.error} /> : null}
      {runs.data?.items.length === 0 ? (
        <EmptyState title="評価 run がありません">上のフォームから評価を実行すると、結果と metric がここに表示されます。</EmptyState>
      ) : null}
      {runs.data && runs.data.items.length > 0 ? (
        <>
          <div className="comparison-toolbar">
            <span className="muted">
              比較する run を 2 件選択してください。選択順に base / candidate として扱います。
            </span>
            <button type="button" disabled={selectedRunIds.length !== 2} onClick={openComparison}>
              比較
            </button>
            <button
              type="button"
              disabled={selectedRunIds.length === 0}
              onClick={() => setSelectedRunIds([])}
            >
              選択解除
            </button>
          </div>
          <table className="admin-table">
            <thead>
              <tr>
                <th>評価 run</th>
                <th>比較</th>
                <th>dataset</th>
                <th>strategy</th>
                <th>状態</th>
                <th>ケース</th>
                <th>
                  <span className="metric-heading">
                    Metrics
                    <MetricHelp metricName="metric_summary" />
                  </span>
                </th>
                <th>
                  <span className="metric-heading">
                    推定コスト
                    <HelpTooltip
                      description="評価 run の成功ケースで記録された LLM 生成コストの概算合計です。"
                      direction="usage や pricing が取得できない場合は - になります。"
                      title="推定コスト（概算）"
                    />
                  </span>
                </th>
                <th>ジョブ</th>
                <th>開始日時</th>
                <th>終了日時</th>
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
                      aria-label={`比較対象 run #${run.evaluation_run_id} を選択`}
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
                    成功 {run.succeeded_count}/{run.case_count}
                    {run.failed_count ? ` / 失敗 ${run.failed_count}` : ""}
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
        {datasets.isLoading ? <LoadingState label="dataset を読み込んでいます..." /> : null}
        {datasets.error ? <ErrorState error={datasets.error} /> : null}
        {datasets.data && datasets.data.items.length > 0 ? (
          <table className="admin-table">
            <thead>
              <tr>
                <th>dataset</th>
                <th>状態</th>
                <th>source</th>
                <th>ケース</th>
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
