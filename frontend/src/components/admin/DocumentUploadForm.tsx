import { FormEvent, useState } from "react";
import { InlineAlert } from "../common/States";
import { useUploadDocument } from "../../features/documents/documentHooks";
import type { DocumentUploadResponse } from "../../features/documents/documentTypes";

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".markdown", ".csv"];

function validateFile(file: File | null): string | null {
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
    const fileError = validateFile(file);
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
