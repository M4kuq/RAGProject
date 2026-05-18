import { apiFetch } from "../../lib/apiClient";
import { CurrentUser } from "./authTypes";

type ApiResponse<T> = {
  data: T;
  meta?: { request_id?: string };
};

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiFetch<ApiResponse<CurrentUser>>("/api/v1/auth/me");
  return response.data;
}
