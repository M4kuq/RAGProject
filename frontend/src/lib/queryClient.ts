import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./apiClient";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && [401, 403, 404, 409, 422].includes(error.status)),
      staleTime: 10_000
    },
    mutations: { retry: 0 }
  }
});
