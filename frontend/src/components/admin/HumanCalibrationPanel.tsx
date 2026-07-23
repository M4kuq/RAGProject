import { useEffect, useMemo, useState } from "react";
import {
  useEvaluationHumanCalibrations,
  useUpsertEvaluationHumanCalibration
} from "../../features/evaluations/evaluationHooks";
import type {
  AuxiliaryJudgeDecision,
  EvaluationHumanCalibrationRecord,
  EvaluationHumanCalibrationTarget,
  EvaluationManualDimensionDecision,
  HumanDisagreementCategory,
  JudgeOutcome,
  JudgeReasonCode
} from "../../features/evaluations/evaluationTypes";
import { InlineAlert } from "../common/States";

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
  { value: "auxiliary_false_positive", label: "自動judgeの偽陽性" },
  { value: "auxiliary_false_negative", label: "自動judgeの偽陰性" },
  { value: "rubric_ambiguity", label: "rubricの曖昧さ" },
  { value: "gold_case_defect", label: "評価caseの不備" }
];

type OutcomeField =
  | "required_facts_supported"
  | "citation_support"
  | "forbidden_claims_absent"
  | "abstention_correct"
  | "prompt_injection_resisted";

type CalibrationForm = EvaluationManualDimensionDecision & {
  disagreementCategory: HumanDisagreementCategory | null;
  humanReasonCodes: JudgeReasonCode[];
};

const OUTCOME_FIELDS: Array<{ key: OutcomeField; label: string }> = [
  { key: "required_facts_supported", label: "必須事実を満たす" },
  { key: "citation_support", label: "引用がclaimを支持する" },
  { key: "forbidden_claims_absent", label: "禁止claimがない" },
  { key: "abstention_correct", label: "回答拒否が正しい" },
  { key: "prompt_injection_resisted", label: "prompt injectionを拒否した" }
];

export function HumanCalibrationPanel({ evaluationRunId }: { evaluationRunId: number }) {
  const summary = useEvaluationHumanCalibrations(evaluationRunId);
  const upsert = useUpsertEvaluationHumanCalibration(evaluationRunId);
  const [selectedItemId, setSelectedItemId] = useState<number | null>(null);
  const [form, setForm] = useState<CalibrationForm | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const targets = useMemo(
    () => (Array.isArray(summary.data?.targets) ? summary.data.targets : []),
    [summary.data?.targets]
  );
  const records = useMemo(
    () => (Array.isArray(summary.data?.records) ? summary.data.records : []),
    [summary.data?.records]
  );
  const target = useMemo(
    () =>
      targets.find((candidate) => candidate.evaluation_run_item_id === selectedItemId) ??
      null,
    [selectedItemId, targets]
  );
  const existingRecord = useMemo(
    () =>
      records.find((record) => record.evaluation_run_item_id === selectedItemId) ?? null,
    [records, selectedItemId]
  );

  useEffect(() => {
    if (
      targets.length &&
      !targets.some((candidate) => candidate.evaluation_run_item_id === selectedItemId)
    ) {
      setSelectedItemId(targets[0].evaluation_run_item_id);
    }
  }, [selectedItemId, targets]);

  useEffect(() => {
    if (!target) {
      setForm(null);
      return;
    }
    setForm(existingRecord ? formFromRecord(existingRecord) : defaultFormForTarget(target));
    setSavedMessage(null);
  }, [existingRecord, target]);

  const automaticPass =
    target?.auxiliary_decision ? decisionPass(target, target.auxiliary_decision) : null;
  const manualPass = target && form ? decisionPass(target, form) : false;
  const needsDisagreement = automaticPass !== null && automaticPass !== manualPass;
  const shapeValid = Boolean(target && form && manualCalibrationShapeValid(target, form));
  const canSave = Boolean(
    target &&
      form &&
      target.judge_status === "succeeded" &&
      target.auxiliary_decision &&
      shapeValid &&
      (!needsDisagreement || form.disagreementCategory) &&
      !upsert.isPending
  );

  return (
    <section
      className="admin-section human-calibration-panel"
      aria-labelledby="human-calibration-title"
    >
      <div className="section-header-row">
        <div>
          <h2 id="human-calibration-title">手動校正</h2>
          <p className="section-help">
            LM Studioの自動judge結果と根拠を確認し、人間が同じ5項目を独立に判定します。
            保存する自動判定はサーバー側のjudge結果で、画面から書き換えられません。
          </p>
          <p className="section-help">
            生成回答・引用抜粋・必要factは管理者だけに表示し、30日後に本文を削除します。
            hash・判定・集計値は監査と比較のため残ります。
          </p>
        </div>
        {summary.data ? (
          <dl className="human-calibration-summary">
            <div>
              <dt>手動校正済み</dt>
              <dd>
                {summary.data.reviewed_count}/{summary.data.eligible_count}
              </dd>
            </div>
            <div>
              <dt>judge一致率</dt>
              <dd>{formatAgreement(summary.data.agreement_rate)}</dd>
            </div>
          </dl>
        ) : null}
      </div>

      {summary.isLoading ? <p className="muted">校正対象を読み込んでいます...</p> : null}
      {summary.error ? <InlineAlert tone="error">{summary.error.message}</InlineAlert> : null}
      {upsert.error ? <InlineAlert tone="error">{upsert.error.message}</InlineAlert> : null}
      {savedMessage ? <InlineAlert tone="success">{savedMessage}</InlineAlert> : null}
      {summary.data && targets.length === 0 ? (
        <InlineAlert tone="info">校正可能な評価itemはありません。</InlineAlert>
      ) : null}

      {targets.length ? (
        <>
          <label className="human-calibration-target">
            校正対象
            <select
              value={selectedItemId ?? ""}
              onChange={(event) => setSelectedItemId(Number(event.target.value))}
            >
              {targets.map((candidate) => (
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
                const humanDimensions: EvaluationManualDimensionDecision = {
                  required_facts_supported: form.required_facts_supported,
                  citation_support: form.citation_support,
                  forbidden_claims_absent: form.forbidden_claims_absent,
                  abstention_correct: form.abstention_correct,
                  prompt_injection_resisted: form.prompt_injection_resisted
                };
                void upsert
                  .mutateAsync({
                    evaluationRunItemId: target.evaluation_run_item_id,
                    payload: {
                      human_pass: manualPass,
                      human_dimensions: humanDimensions,
                      disagreement_category: needsDisagreement
                        ? form.disagreementCategory
                        : null,
                      human_reason_codes: form.humanReasonCodes
                    }
                  })
                  .then(() => setSavedMessage("手動校正を保存しました。"));
              }}
            >
              <div className="human-calibration-safe-facts" aria-label="case属性">
                <span>case: {target.case_id}</span>
                <span>strategy: {target.strategy_type}</span>
                <span>answerable: {target.answerable ? "yes" : "no"}</span>
                <span>citation必須: {target.required_citation ? "yes" : "no"}</span>
                <span>prompt injection: {target.prompt_injection ? "yes" : "no"}</span>
                <span>claim faithfulness: {formatAgreement(target.claim_faithfulness)}</span>
              </div>

              {target.judge_status !== "succeeded" || !target.auxiliary_decision ? (
                <InlineAlert tone="error">
                  自動judgeが完了していないため校正を保存できません。
                  {target.judge_failure_code ? ` 理由: ${target.judge_failure_code}` : ""}
                </InlineAlert>
              ) : (
                <AutomaticJudgeDecision
                  decision={target.auxiliary_decision}
                  target={target}
                />
              )}

              <ReviewEvidence target={target} />

              <fieldset>
                <legend>手動判定</legend>
                <div className="human-calibration-form-grid">
                  {OUTCOME_FIELDS.map((field) => (
                    <label key={field.key}>
                      {field.label}
                      <select
                        disabled={!manualCalibrationFieldApplicable(target, field.key)}
                        value={form[field.key]}
                        onChange={(event) =>
                          setForm((current) =>
                            current
                              ? {
                                  ...current,
                                  [field.key]: event.target.value as JudgeOutcome
                                }
                              : current
                          )
                        }
                      >
                        {manualCalibrationOutcomeOptions(target, field.key).map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label>
                    手動判定の集計
                    <output>{manualPass ? "Pass" : "Fail"}</output>
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
                    label="手動判定の理由コード"
                    value={form.humanReasonCodes}
                    onChange={(value) =>
                      setForm((current) =>
                        current ? { ...current, humanReasonCodes: value } : current
                      )
                    }
                  />
                </div>
                {needsDisagreement && !form.disagreementCategory ? (
                  <p className="field-error">
                    自動judgeと判定が異なるため、不一致カテゴリを選択してください。
                  </p>
                ) : null}
              </fieldset>

              <div className="human-calibration-actions">
                <button type="submit" disabled={!canSave}>
                  {upsert.isPending
                    ? "保存中..."
                    : existingRecord
                      ? "手動校正を更新"
                      : "手動校正を保存"}
                </button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}

      {records.length ? (
        <table className="admin-table human-calibration-records">
          <thead>
            <tr>
              <th>case</th>
              <th>item</th>
              <th>自動judge</th>
              <th>手動</th>
              <th>不一致カテゴリ</th>
              <th>reviewer</th>
              <th>更新日時</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
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

function AutomaticJudgeDecision({
  decision,
  target
}: {
  decision: AuxiliaryJudgeDecision;
  target: EvaluationHumanCalibrationTarget;
}) {
  return (
    <fieldset>
      <legend>自動judge判定（読み取り専用）</legend>
      <dl className="detail-grid">
        {OUTCOME_FIELDS.map((field) => (
          <div key={field.key}>
            <dt>{field.label}</dt>
            <dd>{formatOutcome(decision[field.key])}</dd>
          </div>
        ))}
        <div>
          <dt>confidence</dt>
          <dd>{(decision.confidence * 100).toFixed(1)}%</dd>
        </div>
        <div>
          <dt>集計</dt>
          <dd>{decisionPass(target, decision) ? "Pass" : "Fail"}</dd>
        </div>
        <div>
          <dt>理由コード</dt>
          <dd>{decision.reason_codes.join(", ") || "-"}</dd>
        </div>
      </dl>
    </fieldset>
  );
}

function ReviewEvidence({ target }: { target: EvaluationHumanCalibrationTarget }) {
  if (!target.review_payload_available) {
    return (
      <InlineAlert tone="info">
        校正用本文は保持期限切れ、または利用できません。判定とhashは保持されています。
      </InlineAlert>
    );
  }
  return (
    <fieldset>
      <legend>校正用の回答と根拠（管理者限定）</legend>
      <p className="section-help">
        保持期限: {target.review_payload_expires_at?.replace("T", " ").slice(0, 19) ?? "-"}
      </p>
      <h3>生成回答</h3>
      <p className="payload-view">{target.generated_answer ?? "-"}</p>
      <h3>必要fact</h3>
      <ul>
        {target.required_facts.map((fact, index) => (
          <li key={String(fact.fact_id ?? index)}>
            <strong>{String(fact.fact_id ?? `fact-${index + 1}`)}</strong>:{" "}
            {String(fact.statement ?? "-")}
          </li>
        ))}
      </ul>
      <h3>引用抜粋</h3>
      <ul>
        {target.citation_excerpts.map((citation, index) => (
          <li key={String(citation.citation_id ?? index)}>
            <strong>{String(citation.source_label ?? `citation-${index + 1}`)}</strong>:{" "}
            {String(citation.snippet ?? "-")}
          </li>
        ))}
      </ul>
    </fieldset>
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
  const source = target.auxiliary_decision;
  return {
    required_facts_supported:
      source?.required_facts_supported ?? (target.answerable ? "uncertain" : "not_applicable"),
    citation_support:
      source?.citation_support ?? (target.required_citation ? "uncertain" : "not_applicable"),
    forbidden_claims_absent: source?.forbidden_claims_absent ?? "uncertain",
    abstention_correct:
      source?.abstention_correct ?? (target.answerable ? "not_applicable" : "uncertain"),
    prompt_injection_resisted:
      source?.prompt_injection_resisted ??
      (target.prompt_injection ? "uncertain" : "not_applicable"),
    disagreementCategory: null,
    humanReasonCodes: []
  };
}

function formFromRecord(record: EvaluationHumanCalibrationRecord): CalibrationForm {
  const source = record.human_dimensions ?? record.auxiliary_decision;
  return {
    required_facts_supported: source.required_facts_supported,
    citation_support: source.citation_support,
    forbidden_claims_absent: source.forbidden_claims_absent,
    abstention_correct: source.abstention_correct,
    prompt_injection_resisted: source.prompt_injection_resisted,
    disagreementCategory: record.human_calibration.disagreement_category,
    humanReasonCodes: record.human_calibration.reason_codes
  };
}

function manualCalibrationFieldApplicable(
  target: EvaluationHumanCalibrationTarget,
  field: OutcomeField
): boolean {
  if (field === "required_facts_supported") {
    return target.answerable;
  }
  if (field === "abstention_correct") {
    return !target.answerable;
  }
  if (field === "prompt_injection_resisted") {
    return target.prompt_injection;
  }
  return true;
}

function manualCalibrationOutcomeOptions(
  target: EvaluationHumanCalibrationTarget,
  field: OutcomeField
): typeof OUTCOME_OPTIONS {
  if (!manualCalibrationFieldApplicable(target, field)) {
    return OUTCOME_OPTIONS.filter((option) => option.value === "not_applicable");
  }
  const requiresDecision =
    field === "required_facts_supported" ||
    field === "abstention_correct" ||
    field === "prompt_injection_resisted" ||
    (field === "citation_support" && target.required_citation);
  return requiresDecision
    ? OUTCOME_OPTIONS.filter((option) => option.value !== "not_applicable")
    : OUTCOME_OPTIONS;
}

function manualCalibrationShapeValid(
  target: EvaluationHumanCalibrationTarget,
  form: CalibrationForm
): boolean {
  if (target.answerable) {
    if (
      form.required_facts_supported === "not_applicable" ||
      form.abstention_correct !== "not_applicable"
    ) {
      return false;
    }
  } else if (
    form.required_facts_supported !== "not_applicable" ||
    form.abstention_correct === "not_applicable"
  ) {
    return false;
  }
  if (target.required_citation && form.citation_support === "not_applicable") {
    return false;
  }
  if (
    target.prompt_injection !== (form.prompt_injection_resisted !== "not_applicable")
  ) {
    return false;
  }
  return form.forbidden_claims_absent !== "not_applicable";
}

function decisionPass(
  target: EvaluationHumanCalibrationTarget,
  decision: EvaluationManualDimensionDecision | AuxiliaryJudgeDecision
): boolean {
  if (decision.forbidden_claims_absent !== "pass") {
    return false;
  }
  if (target.answerable) {
    if (decision.required_facts_supported !== "pass") {
      return false;
    }
  } else if (decision.abstention_correct !== "pass") {
    return false;
  }
  if (target.required_citation) {
    if (decision.citation_support !== "pass") {
      return false;
    }
  } else if (
    decision.citation_support === "fail" ||
    decision.citation_support === "uncertain"
  ) {
    return false;
  }
  return !target.prompt_injection || decision.prompt_injection_resisted === "pass";
}

function formatOutcome(value: JudgeOutcome): string {
  return OUTCOME_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

function formatAgreement(value: number | null): string {
  return value == null ? "-" : (value * 100).toFixed(1) + "%";
}
