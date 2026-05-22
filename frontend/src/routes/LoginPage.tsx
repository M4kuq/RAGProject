import { useState } from "react";
import { useForm } from "react-hook-form";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../lib/apiClient";
import { useSetCurrentUser } from "../features/auth/authHooks";
import type { CurrentUser } from "../features/auth/authTypes";
import type { ApiResponse } from "../types/api";

type LoginForm = { email: string; password: string };
type LoginLocationState = { from?: { pathname?: string; search?: string; hash?: string } };

function getRedirectTarget(state: unknown): string {
  const from = (state as LoginLocationState | null)?.from;
  if (!from?.pathname?.startsWith("/")) {
    return "/chat";
  }
  return `${from.pathname}${from.search ?? ""}${from.hash ?? ""}`;
}

export function LoginPage() {
  const { register, handleSubmit } = useForm<LoginForm>({
    defaultValues: { email: "admin@example.com", password: "password" }
  });
  const [error, setError] = useState<string | null>(null);
  const setCurrentUser = useSetCurrentUser();
  const location = useLocation();
  const navigate = useNavigate();

  async function onSubmit(values: LoginForm) {
    setError(null);
    await apiFetch("/api/v1/auth/csrf");
    const response = await apiFetch<ApiResponse<{ user: CurrentUser; csrf_token: string }>>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(values)
    }).catch((err) => {
      setError(err.message);
      return null;
    });
    if (response) {
      setCurrentUser(response.data.user);
      navigate(getRedirectTarget(location.state), { replace: true });
    }
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
