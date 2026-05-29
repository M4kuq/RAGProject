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
    return "Select a file.";
  }
  const lowerName = file.name.toLowerCase();
  if (!ALLOWED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return `Allowed extensions: ${ALLOWED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_FILE_BYTES) {
    return "File size must be 20 MB or less.";
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
      setClientError("Enter a title.");
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
      <h2>Upload</h2>
      <form className="stack upload-form" onSubmit={submit}>
        <label>
          Title
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} />
        </label>
        <label>
          File
          <input
            aria-label="file"
            type="file"
            accept={ALLOWED_EXTENSIONS.join(",")}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <p className="muted">Allowed: {ALLOWED_EXTENSIONS.join(", ")} / Max: 20 MB</p>
        <button type="submit" disabled={upload.isPending}>
          {upload.isPending ? "Uploading..." : "Upload"}
        </button>
      </form>
      {clientError ? <InlineAlert tone="error">{clientError}</InlineAlert> : null}
      {upload.error ? <InlineAlert tone="error">{upload.error.message}</InlineAlert> : null}
      {success ? (
        <InlineAlert tone="success">
          Uploaded document #{success.logical_document_id}, version #{success.document_version_id}, job #
          {success.job_id}.
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
      setClientError("Enter a URL.");
      return;
    }
    if (!/^https?:\/\//i.test(urlValue)) {
      setClientError("Only http and https URLs are supported.");
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
      <h2>URL ingest</h2>
      <form className="stack upload-form" onSubmit={submit}>
        <label>
          URL
          <input value={url} onChange={(event) => setUrl(event.target.value)} maxLength={2048} />
        </label>
        <label>
          Title
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} />
        </label>
        <p className="muted">Allowed: http / https HTML or XML. Private and localhost targets are rejected.</p>
        <button type="submit" disabled={ingest.isPending}>
          {ingest.isPending ? "Fetching..." : "Fetch URL"}
        </button>
      </form>
      {clientError ? <InlineAlert tone="error">{clientError}</InlineAlert> : null}
      {ingest.error ? <InlineAlert tone="error">{ingest.error.message}</InlineAlert> : null}
      {success ? (
        <InlineAlert tone="success">
          URL accepted as document #{success.logical_document_id}, version #{success.document_version_id}, job #
          {success.job_id}.
        </InlineAlert>
      ) : null}
    </section>
  );
}
