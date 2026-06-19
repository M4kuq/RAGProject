import { FormEvent, useState } from "react";
import { InlineAlert } from "../common/States";
import { useIngestDocumentUrl, useUploadDocument } from "../../features/documents/documentHooks";
import type { DocumentUploadResponse } from "../../features/documents/documentTypes";

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const ALLOWED_EXTENSIONS = [
  ".pdf",
  ".docx",
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".xlsx",
  ".pptx",
  ".html",
  ".htm",
  ".xml",
];

export function validateDocumentFile(file: File | null): string | null {
  if (!file) {
    return "ファイルを選択してください。";
  }
  const lowerName = file.name.toLowerCase();
  if (!ALLOWED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return `利用できる拡張子: ${ALLOWED_EXTENSIONS.join(", ")}。`;
  }
  if (file.size > MAX_FILE_BYTES) {
    return "ファイルサイズは 20 MB 以下にしてください。";
  }
  return null;
}

export function DocumentUploadForm({ onUploaded }: { onUploaded?: (result: DocumentUploadResponse) => void }) {
  const upload = useUploadDocument();
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);
  const [success, setSuccess] = useState<DocumentUploadResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const titleValue = title.trim();
    const fileError = validateDocumentFile(file);
    if (!titleValue) {
      setClientError("タイトルを入力してください。");
      return;
    }
    if (fileError) {
      setClientError(fileError);
      return;
    }
    setClientError(null);
    try {
      const result = await upload.mutateAsync({ title: titleValue, file: file as File });
      setSuccess(result);
      onUploaded?.(result);
    } catch {
      setSuccess(null);
    }
  }

  return (
    <section className="admin-section">
      <h2>ファイルをアップロード</h2>
      <p className="section-help">社内文書を取り込み、処理完了後に版として確認できます。</p>
      <form className="stack upload-form" onSubmit={submit}>
        <label>
          タイトル
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} />
        </label>
        <label>
          ファイル
          <input
            aria-label="アップロードファイル"
            type="file"
            accept={ALLOWED_EXTENSIONS.join(",")}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <p className="muted">利用可能: {ALLOWED_EXTENSIONS.join(", ")} / 上限: 20 MB</p>
        <button type="submit" disabled={upload.isPending}>
          {upload.isPending ? "アップロード中..." : "アップロード"}
        </button>
      </form>
      {clientError ? <InlineAlert tone="error">{clientError}</InlineAlert> : null}
      {upload.error ? <InlineAlert tone="error">{upload.error.message}</InlineAlert> : null}
      {success ? (
        <InlineAlert tone="success">
          ドキュメント #{success.logical_document_id} / v{success.document_version_id} を登録しました。ジョブ #
          {success.job_id} で処理します。
        </InlineAlert>
      ) : null}
    </section>
  );
}

export function DocumentUrlIngestForm({ onIngested }: { onIngested?: (result: DocumentUploadResponse) => void }) {
  const ingest = useIngestDocumentUrl();
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);
  const [success, setSuccess] = useState<DocumentUploadResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const urlValue = url.trim();
    const titleValue = title.trim();
    if (!urlValue) {
      setClientError("URL を入力してください。");
      return;
    }
    if (!/^https?:\/\//i.test(urlValue)) {
      setClientError("http / https の URL だけ利用できます。");
      return;
    }
    setClientError(null);
    try {
      const result = await ingest.mutateAsync({
        url: urlValue,
        title: titleValue || undefined
      });
      setSuccess(result);
      onIngested?.(result);
    } catch {
      setSuccess(null);
    }
  }

  return (
    <section className="admin-section">
      <h2>URL から取り込み</h2>
      <p className="section-help">公開 HTML / XML を取り込みます。private / localhost 宛ては拒否されます。</p>
      <form className="stack upload-form" onSubmit={submit}>
        <label>
          URL
          <input value={url} onChange={(event) => setUrl(event.target.value)} maxLength={2048} />
        </label>
        <label>
          タイトル
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} />
        </label>
        <p className="muted">利用可能: http / https の HTML または XML</p>
        <button type="submit" disabled={ingest.isPending}>
          {ingest.isPending ? "取得中..." : "URL を取り込む"}
        </button>
      </form>
      {clientError ? <InlineAlert tone="error">{clientError}</InlineAlert> : null}
      {ingest.error ? <InlineAlert tone="error">{ingest.error.message}</InlineAlert> : null}
      {success ? (
        <InlineAlert tone="success">
          URL をドキュメント #{success.logical_document_id} / v{success.document_version_id} として登録しました。ジョブ #
          {success.job_id} で処理します。
        </InlineAlert>
      ) : null}
    </section>
  );
}
