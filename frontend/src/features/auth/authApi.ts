import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type { CurrentUser } from "./authTypes";

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiFetch<ApiResponse<CurrentUser>>("/api/v1/auth/me");
  return response.data;
}

export async function getCsrfToken(): Promise<string> {
  const response = await apiFetch<ApiResponse<{ csrf_token: string }>>("/api/v1/auth/csrf");
  return response.data.csrf_token;
}
