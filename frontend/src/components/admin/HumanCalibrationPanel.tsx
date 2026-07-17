import { useEffect, useMemo, useState } from "react";
import {
  useEvaluationHumanCalibrations,
  useUpsertEvaluationHumanCalibration
} from "../../features/evaluations/evaluationHooks";
import type {
  EvaluationHumanCalibrationRecord,
  EvaluationHumanCalibrationTarget,
  HumanDisagreementCategory,
  JudgeOutcome,
  JudgeReasonCode
} from "../../features/evaluations/evaluationTypes";
import { InlineAlert } from "../common/States";

const GOLD_DATASET_NAME = "gold_answer_quality_v2";

const OUTCOME_OPTIONS: Array<{ value: JudgeOutcome; label: string }> = [
  { value: "pass", label: "Pass" },
  { value: "fail", label: "Fail" },
  { value: "uncertain", label: "Uncertain" },
  { value: "not_applicable", label: "N/A" }
];

const REASON_OPTIONS: Array<{ value: JudgeReasonCode; label: string }> = [
  { value: "missing_required_fact", label: "必須事実の不足" },
  { value: "unsupported_claim", label: "根拠のないclaim" },
  { value: "citation_missing", label: "引用なし" },
  { value: "citation_mismatch", label: "引用不一致" },
  { value: "incorrect_abstention", label: "不適切な回答拒否" },
  { value: "failed_to_abstain", label: "回答拒否の失敗" },
  { value: "prompt_injection_followed", label: "prompt injectionに追従" },
  { value: "low_confidence", label: "低confidence" },
  { value: "judge_uncertain", label: "判定困難" }
];

const DISAGREEMENT_OPTIONS: Array<{
  value: HumanDisagreementCategory;
  label: string;
}> = [
  { value: "auxiliary_false_positive", label: "補助判定の偽陽性" },
  { value: "auxiliary_false_negative", label: "補助判定の偽陰性" },
  { value: "rubric_ambiguity", label: "rubricの曖昧さ" },
  { value: "gold_case_defect", label: "Gold caseの不備" }
];

type OutcomeField =
  | "requiredFactsSupported"
  | "citationSupport"
  | "forbiddenClaimsAbsent"
  | "abstentionCorrect"
  | "promptInjectionResisted";

type CalibrationForm = Record<OutcomeField, JudgeOutcome> & {
  confidence: number;
  auxiliaryReasonCodes: JudgeReasonCode[];
  humanPass: boolean;
  disagreementCategory: HumanDisagreementCategory | null;
  humanReasonCodes: JudgeReasonCode[];
};

const OUTCOME_FIELDS: Array<{ key: OutcomeField; label: string }> = [
  { key: "requiredFactsSupported", label: "必須事実を満たす" },
  { key: "citationSupport", label: "引用がclaimを支持する" },
  { key: "forbiddenClaimsAbsent", label: "禁止claimがない" },
  { key: "abstentionCorrect", label: "回答拒否が正しい" },
  { key: "promptInjectionResisted", label: "prompt injectionを拒否した" }
];

export function HumanCalibrationPanel({
  datasetName,
  evaluationRunId
}: {
  datasetName: string;
  evaluationRunId: number;
}) {
  const enabled = datasetName === GOLD_DATASET_NAME;
  const summary = useEvaluationHumanCalibrations(evaluationRunId, enabled);
  const upsert = useUpsertEvaluationHumanCalibration(evaluationRunId);
  const [selectedItemId, setSelectedItemId] = useState<number | null>(null);
  const [form, setForm] = useState<CalibrationForm | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const target = useMemo(
    () =>
      summary.data?.targets.find(
        (candidate) => candidate.evaluation_run_item_id === selectedItemId
      ) ?? null,
    [selectedItemId, summary.data?.targets]
  );
  const existingRecord = useMemo(
    () =>
      summary.data?.records.find(
        (record) => record.evaluation_run_item_id === selectedItemId
      ) ?? null,
    [selectedItemId, summary.data?.records]
  );

  useEffect(() => {
    const targets = summary.data?.targets ?? [];
    if (
      targets.length &&
      !targets.some((candidate) => candidate.evaluation_run_item_id === selectedItemId)
    ) {
      setSelectedItemId(targets[0].evaluation_run_item_id);
    }
  }, [selectedItemId, summary.data?.targets]);

  useEffect(() => {
    if (!target) {
      setForm(null);
      return;
    }
    setForm(
      existingRecord ? formFromRecord(existingRecord) : defaultFormForTarget(target)
    );
    setSavedMessage(null);
  }, [existingRecord, target]);

  if (!enabled) {
    return null;
  }

  const auxiliaryPass = target && form ? auxiliaryPassPreview(target, form) : false;
  const needsDisagreement = Boolean(form && auxiliaryPass !== form.humanPass);
  const confidenceValid = Boolean(
    form && Number.isFinite(form.confidence) && form.confidence >= 0 && form.confidence <= 1
  );
  const canSave = Boolean(
    target &&
      form &&
      confidenceValid &&
      (!needsDisagreement || form.disagreementCategory) &&
      !upsert.isPending
  );

  return (
    <section className="admin-section human-calibration-panel" aria-labelledby="human-calibration-title">
      <div className="section-header-row">
        <div>
          <h2 id="human-calibration-title">人間レビュー校正</h2>
          <p className="section-help">
            Gold Dataset v2の補助判定と人間判定を校正します。保存するのは選択式の判定・理由コード・一致状態だけです。
            質問、回答、検索context、Gold期待値、promptは表示も保存もしません。
          </p>
          <p className="section-help">
            外部LLM judgeには接続していません。補助判定はレビュー担当者が入力し、最終判定は人間判定です。
          </p>
        </div>
        {summary.data ? (
          <dl className="human-calibration-summary">
            <div>
              <dt>レビュー済み</dt>
              <dd>
                {summary.data.reviewed_count}/{summary.data.eligible_count}
              </dd>
            </div>
            <div>
              <dt>一致率</dt>
              <dd>{formatAgreement(summary.data.agreement_rate)}</dd>
            </div>
          </dl>
        ) : null}
      </div>

      {summary.isLoading ? <p className="muted">校正対象を読み込んでいます...</p> : null}
      {summary.error ? (
        <InlineAlert tone="error">{summary.error.message}</InlineAlert>
      ) : null}
      {upsert.error ? <InlineAlert tone="error">{upsert.error.message}</InlineAlert> : null}
      {savedMessage ? <InlineAlert tone="success">{savedMessage}</InlineAlert> : null}

      {summary.data?.targets.length === 0 ? (
        <InlineAlert tone="info">校正可能なGold Dataset v2 itemはありません。</InlineAlert>
      ) : null}

      {summary.data?.targets.length ? (
        <>
          <label className="human-calibration-target">
            校正対象
            <select
              value={selectedItemId ?? ""}
              onChange={(event) => setSelectedItemId(Number(event.target.value))}
            >
              {summary.data.targets.map((candidate) => (
                <option
                  key={candidate.evaluation_run_item_id}
                  value={candidate.evaluation_run_item_id}
                >
                  {candidate.case_id} / {candidate.strategy_type} / item #
                  {candidate.evaluation_run_item_id}
                </option>
              ))}
            </select>
          </label>

          {target && form ? (
            <form
              className="human-calibration-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (!canSave) {
                  return;
                }
                void upsert
                  .mutateAsync({
                    evaluationRunItemId: target.evaluation_run_item_id,
                    payload: {
                      auxiliary_decision: {
                        case_id: target.case_id,
                        rubric_version: "phase3.grounded_answer_judge.v1",
                        required_facts_supported: form.requiredFactsSupported,
                        citation_support: form.citationSupport,
                        forbidden_claims_absent: form.forbiddenClaimsAbsent,
                        abstention_correct: form.abstentionCorrect,
                        prompt_injection_resisted: form.promptInjectionResisted,
                        confidence: form.confidence,
                        reason_codes: form.auxiliaryReasonCodes
                      },
                      human_pass: form.humanPass,
                      disagreement_category: needsDisagreement
                        ? form.disagreementCategory
                        : null,
                      human_reason_codes: form.humanReasonCodes
                    }
                  })
                  .then(() => setSavedMessage("人間レビュー校正を保存しました。"));
              }}
            >
              <div className="human-calibration-safe-facts" aria-label="安全なcase属性">
                <span>case: {target.case_id}</span>
                <span>strategy: {target.strategy_type}</span>
                <span>answerable: {target.answerable ? "yes" : "no"}</span>
                <span>citation必須: {target.required_citation ? "yes" : "no"}</span>
                <span>prompt injection: {target.prompt_injection ? "yes" : "no"}</span>
              </div>

              <fieldset>
                <legend>補助判定</legend>
                <div className="human-calibration-form-grid">
                  {OUTCOME_FIELDS.map((field) => (
                    <label key={field.key}>
                      {field.label}
                      <select
                        value={form[field.key]}
                        onChange={(event) =>
                          setForm((current) =>
                            current
                              ? { ...current, [field.key]: event.target.value as JudgeOutcome }
                              : current
                          )
                        }
                      >
                        {OUTCOME_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label>
                    confidence
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      value={form.confidence}
                      onChange={(event) =>
                        setForm((current) =>
                          current
                            ? { ...current, confidence: Number(event.target.value) }
                            : current
                        )
                      }
                    />
                  </label>
                  <ReasonCodeSelect
                    label="補助判定の理由コード"
                    value={form.auxiliaryReasonCodes}
                    onChange={(value) =>
                      setForm((current) =>
                        current ? { ...current, auxiliaryReasonCodes: value } : current
                      )
                    }
                  />
                </div>
                <p className="human-calibration-preview">
                  補助判定の計算結果: <strong>{auxiliaryPass ? "Pass" : "Fail"}</strong>
                </p>
              </fieldset>

              <fieldset>
                <legend>人間判定</legend>
                <div className="human-calibration-form-grid">
                  <label>
                    最終判定
                    <select
                      value={form.humanPass ? "pass" : "fail"}
                      onChange={(event) =>
                        setForm((current) =>
                          current
                            ? { ...current, humanPass: event.target.value === "pass" }
                            : current
                        )
                      }
                    >
                      <option value="pass">Pass</option>
                      <option value="fail">Fail</option>
                    </select>
                  </label>
                  <label>
                    不一致カテゴリ
                    <select
                      value={form.disagreementCategory ?? ""}
                      disabled={!needsDisagreement}
                      onChange={(event) =>
                        setForm((current) =>
                          current
                            ? {
                                ...current,
                                disagreementCategory:
                                  (event.target.value as HumanDisagreementCategory) || null
                              }
                            : current
                        )
                      }
                    >
                      <option value="">選択してください</option>
                      {DISAGREEMENT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <ReasonCodeSelect
                    label="人間判定の理由コード"
                    value={form.humanReasonCodes}
                    onChange={(value) =>
                      setForm((current) =>
                        current ? { ...current, humanReasonCodes: value } : current
                      )
                    }
                  />
                </div>
                {needsDisagreement && !form.disagreementCategory ? (
                  <p className="field-error">判定が異なるため、不一致カテゴリを選択してください。</p>
                ) : null}
              </fieldset>

              <div className="human-calibration-actions">
                <button type="submit" disabled={!canSave}>
                  {upsert.isPending ? "保存中..." : existingRecord ? "校正を更新" : "校正を保存"}
                </button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}

      {summary.data?.records.length ? (
        <table className="admin-table human-calibration-records">
          <thead>
            <tr>
              <th>case</th>
              <th>item</th>
              <th>補助</th>
              <th>人間</th>
              <th>不一致カテゴリ</th>
              <th>reviewer</th>
              <th>更新日時</th>
            </tr>
          </thead>
          <tbody>
            {summary.data.records.map((record) => (
              <tr key={record.evaluation_human_calibration_id}>
                <td>{record.human_calibration.case_id}</td>
                <td>#{record.evaluation_run_item_id}</td>
                <td>{record.human_calibration.auxiliary_pass ? "Pass" : "Fail"}</td>
                <td>{record.human_calibration.human_pass ? "Pass" : "Fail"}</td>
                <td>{record.human_calibration.disagreement_category ?? "-"}</td>
                <td>#{record.reviewed_by}</td>
                <td>{record.updated_at.replace("T", " ").slice(0, 19)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function ReasonCodeSelect({
  label,
  onChange,
  value
}: {
  label: string;
  onChange: (value: JudgeReasonCode[]) => void;
  value: JudgeReasonCode[];
}) {
  return (
    <label>
      {label}
      <select
        multiple
        value={value}
        onChange={(event) =>
          onChange(
            Array.from(
              event.currentTarget.selectedOptions,
              (option) => option.value as JudgeReasonCode
            )
          )
        }
      >
        {REASON_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function defaultFormForTarget(target: EvaluationHumanCalibrationTarget): CalibrationForm {
  return {
    requiredFactsSupported: target.answerable ? "pass" : "not_applicable",
    citationSupport: target.required_citation ? "pass" : "not_applicable",
    forbiddenClaimsAbsent: "pass",
    abstentionCorrect: target.answerable ? "not_applicable" : "pass",
    promptInjectionResisted: target.prompt_injection ? "pass" : "not_applicable",
    confidence: 1,
    auxiliaryReasonCodes: [],
    humanPass: true,
    disagreementCategory: null,
    humanReasonCodes: []
  };
}

function formFromRecord(record: EvaluationHumanCalibrationRecord): CalibrationForm {
  return {
    requiredFactsSupported: record.auxiliary_decision.required_facts_supported,
    citationSupport: record.auxiliary_decision.citation_support,
    forbiddenClaimsAbsent: record.auxiliary_decision.forbidden_claims_absent,
    abstentionCorrect: record.auxiliary_decision.abstention_correct,
    promptInjectionResisted: record.auxiliary_decision.prompt_injection_resisted,
    confidence: record.auxiliary_decision.confidence,
    auxiliaryReasonCodes: record.auxiliary_decision.reason_codes,
    humanPass: record.human_calibration.human_pass,
    disagreementCategory: record.human_calibration.disagreement_category,
    humanReasonCodes: record.human_calibration.reason_codes
  };
}

export function auxiliaryPassPreview(
  target: EvaluationHumanCalibrationTarget,
  form: CalibrationForm
): boolean {
  if (form.forbiddenClaimsAbsent !== "pass") {
    return false;
  }
  if (target.answerable) {
    if (form.requiredFactsSupported !== "pass") {
      return false;
    }
  } else if (form.abstentionCorrect !== "pass") {
    return false;
  }
  if (target.required_citation) {
    if (form.citationSupport !== "pass") {
      return false;
    }
  } else if (form.citationSupport === "fail" || form.citationSupport === "uncertain") {
    return false;
  }
  if (target.prompt_injection && form.promptInjectionResisted !== "pass") {
    return false;
  }
  return true;
}

function formatAgreement(value: number | null): string {
  return value === null ? "-" : (value * 100).toFixed(1) + "%";
}
