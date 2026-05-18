import { useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import { getCurrentUser } from "./authApi";
import type { CurrentUser } from "./authTypes";

export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.currentUser,
    queryFn: getCurrentUser,
    retry: false
  });
}

export function useSetCurrentUser() {
  const queryClient = useQueryClient();
  return (user: CurrentUser) => {
    queryClient.setQueryData(queryKeys.currentUser, user);
  };
}
