import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./apiClient";

// Client-error statuses where retrying cannot help (auth, missing resource,
// conflict, validation), so we skip the automatic retry for these.
const NON_RETRYABLE_STATUSES = [401, 403, 404, 409, 422];

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && NON_RETRYABLE_STATUSES.includes(error.status)),
      staleTime: 10_000
    },
    mutations: { retry: 0 }
  }
});
