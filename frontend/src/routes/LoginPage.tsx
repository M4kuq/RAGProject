import { useState } from "react";
import { useForm } from "react-hook-form";
import { apiFetch } from "../lib/apiClient";

type LoginForm = { email: string; password: string };

export function LoginPage() {
  const { register, handleSubmit } = useForm<LoginForm>({
    defaultValues: { email: "admin@example.com", password: "password" }
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(values: LoginForm) {
    setError(null);
    await apiFetch("/api/v1/auth/csrf");
    await apiFetch("/api/v1/auth/login", { method: "POST", body: JSON.stringify(values) }).catch((err) =>
      setError(err.message)
    );
  }

  return (
    <main className="panel">
      <h1>RAGProject</h1>
      <form onSubmit={handleSubmit(onSubmit)} className="stack">
        <input {...register("email", { required: true })} aria-label="email" />
        <input {...register("password", { required: true })} aria-label="password" type="password" />
        <button type="submit">Login</button>
      </form>
      {error ? <p className="error">{error}</p> : null}
    </main>
  );
}
