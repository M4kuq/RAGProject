import { useState } from "react";
import { apiFetch } from "../lib/apiClient";

export function AdminPage() {
  const [status, setStatus] = useState("");

  async function runEvaluation() {
    const result = await apiFetch<{ data: { evaluation_run_id: number } }>("/api/v1/evaluations/runs", {
      method: "POST",
      body: JSON.stringify({})
    });
    setStatus(`Evaluation #${result.data.evaluation_run_id} completed`);
  }

  return (
    <main className="panel">
      <h1>Admin</h1>
      <button type="button" onClick={runEvaluation}>
        Run evaluation
      </button>
      {status ? <p>{status}</p> : null}
    </main>
  );
}
