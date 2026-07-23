import { FormEvent, useRef, useState } from "react";
import {
  useImportEvaluationDataset,
  usePrepareEvaluationDatasetCorpus,
  useValidateEvaluationDataset
} from "../../features/evaluations/evaluationHooks";
import type {
  EvaluationDatasetManifest,
  EvaluationDatasetValidation
} from "../../features/evaluations/evaluationTypes";
import { InlineAlert } from "../common/States";

export const MAX_EVALUATION_DATASET_IMPORT_BYTES = 2 * 1024 * 1024;

export function EvaluationDatasetImportForm() {
  const importDataset = useImportEvaluationDataset();
  const validateDataset = useValidateEvaluationDataset();
  const prepareCorpus = usePrepareEvaluationDatasetCorpus();
  const formRef = useRef<HTMLFormElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [manifest, setManifest] = useState<EvaluationDatasetManifest | null>(null);
  const [preview, setPreview] = useState<EvaluationDatasetValidation | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  async function selectFile(selectedFile: File | null) {
    setFile(selectedFile);
    setManifest(null);
    setPreview(null);
    setValidationError(null);
    setSuccessMessage(null);
    validateDataset.reset();
    if (!selectedFile) {
      return;
    }
    try {
      const parsed = await readEvaluationDatasetManifestFile(selectedFile);
      const validated = await validateDataset.mutateAsync(parsed);
      setManifest(parsed);
      setPreview(validated);
    } catch (error) {
      setValidationError(
        error instanceof Error ? error.message : "JSONファイルを検証できませんでした。"
      );
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationError(null);
    setSuccessMessage(null);
    if (!file || !manifest || !preview) {
      setValidationError("検証済みのJSONファイルを選択してください。");
      return;
    }
    try {
      const result = await importDataset.mutateAsync(manifest);
      if (manifest.schema_version === "phase3.evaluation_dataset.v2") {
        await prepareCorpus.mutateAsync(result.evaluation_dataset_id);
      }
      const action =
        manifest.schema_version === "phase3.evaluation_dataset.v2"
          ? "インポートし、評価コーパスの準備を開始しました"
          : "インポートしました";
      setSuccessMessage(
        "dataset " +
          result.dataset_name +
          " " +
          result.version +
          " を" +
          action +
          "（" +
          result.case_count +
          " cases）。"
      );
      setFile(null);
      setManifest(null);
      setPreview(null);
      formRef.current?.reset();
    } catch (error) {
      setValidationError(
        error instanceof Error ? error.message : "JSONファイルをインポートできませんでした。"
      );
    }
  }

  const requestError = importDataset.error || validateDataset.error || prepareCorpus.error;

  return (
    <section className="admin-section" aria-labelledby="evaluation-dataset-import-title">
      <h2 id="evaluation-dataset-import-title">データセットJSONをアップロード</h2>
      <p className="section-help">
        v1/v2形式のJSONを最大2MBまで検証します。選択しただけでは保存やindexingを行いません。
        検証プレビューの確認後に、明示的にインポートとコーパス準備を開始します。
      </p>
      <form className="dataset-import-form" onSubmit={(event) => void submit(event)} ref={formRef}>
        <label>
          評価データセットJSON
          <input
            accept=".json,application/json"
            aria-label="評価データセットJSON"
            onChange={(event) => void selectFile(event.target.files?.[0] ?? null)}
            type="file"
          />
        </label>
        <button
          disabled={!preview || importDataset.isPending || prepareCorpus.isPending}
          type="submit"
        >
          {importDataset.isPending || prepareCorpus.isPending
            ? "準備中..."
            : manifest?.schema_version === "phase3.evaluation_dataset.v2"
              ? "インポートして評価コーパスを準備"
              : "JSONをインポート"}
        </button>
      </form>

      {preview ? <DatasetValidationPreview preview={preview} /> : null}
      {validationError ? <InlineAlert tone="error">{validationError}</InlineAlert> : null}
      {requestError && !validationError ? (
        <InlineAlert tone="error">{requestError.message}</InlineAlert>
      ) : null}
      {successMessage ? <InlineAlert tone="success">{successMessage}</InlineAlert> : null}
    </section>
  );
}

function DatasetValidationPreview({ preview }: { preview: EvaluationDatasetValidation }) {
  return (
    <section className="dataset-validation-preview" aria-label="JSON検証プレビュー">
      <h3>JSON検証プレビュー</h3>
      <dl className="detail-grid">
        <div>
          <dt>dataset / version</dt>
          <dd>{preview.dataset_name} / {preview.version}</dd>
        </div>
        <div><dt>case</dt><dd>{preview.composition.case_count}</dd></div>
        <div><dt>source / fact</dt><dd>{preview.composition.source_count} / {preview.composition.fact_count}</dd></div>
        <div><dt>answerable / abstention</dt><dd>{preview.composition.answerable_count} / {preview.composition.unanswerable_count}</dd></div>
        <div><dt>日本語 / English</dt><dd>{preview.composition.language_ja_count} / {preview.composition.language_en_count}</dd></div>
        <div><dt>single-hop / multi-hop</dt><dd>{preview.composition.single_hop_count} / {preview.composition.multi_hop_count}</dd></div>
        <div><dt>サイズ</dt><dd>{formatBytes(preview.serialized_size_bytes)}</dd></div>
        <div>
          <dt>corpus fingerprint</dt>
          <dd><code>{preview.corpus_fingerprint?.slice(0, 16) ?? "shared_legacy"}</code></dd>
        </div>
      </dl>
      {preview.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}
    </section>
  );
}

export async function readEvaluationDatasetManifestFile(
  file: File
): Promise<EvaluationDatasetManifest> {
  if (file.size === 0) {
    throw new Error("JSONファイルが空です。");
  }
  if (file.size > MAX_EVALUATION_DATASET_IMPORT_BYTES) {
    throw new Error("JSONファイルは2MB以下にしてください。");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(await file.text());
  } catch {
    throw new Error("有効なJSONファイルではありません。");
  }
  if (!isRecord(parsed)) {
    throw new Error("JSONのルートはオブジェクトにしてください。");
  }
  if (
    parsed.schema_version !== "phase2.evaluation_dataset.v1" &&
    parsed.schema_version !== "phase3.evaluation_dataset.v2"
  ) {
    throw new Error(
      "schema_versionはphase2.evaluation_dataset.v1またはphase3.evaluation_dataset.v2にしてください。"
    );
  }
  if (!isRecord(parsed.dataset) || typeof parsed.dataset.dataset_name !== "string") {
    throw new Error("dataset.dataset_nameが必要です。");
  }
  if (!Array.isArray(parsed.cases) || parsed.cases.length === 0) {
    throw new Error("casesには1件以上の評価ケースが必要です。");
  }
  if (
    parsed.schema_version === "phase3.evaluation_dataset.v2" &&
    (!Array.isArray(parsed.corpus_documents) || parsed.corpus_documents.length === 0)
  ) {
    throw new Error("v2のcorpus_documentsには1件以上の文書が必要です。");
  }
  return parsed as EvaluationDatasetManifest;
}

function formatBytes(value: number): string {
  return (value / 1024).toFixed(1) + " KB";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
