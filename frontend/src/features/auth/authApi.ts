import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type { CurrentUser } from "./authTypes";

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiFetch<ApiResponse<CurrentUser>>("/api/v1/auth/me");
  return response.data;
}
