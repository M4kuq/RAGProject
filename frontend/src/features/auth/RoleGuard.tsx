import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { useCurrentUser } from "./authHooks";

export function ForbiddenPage({ message = "Forbidden" }: { message?: string }) {
  return (
    <main className="panel">
      <h1>Forbidden</h1>
      <p>{message}</p>
    </main>
  );
}

export function RoleGuard({ children, role }: { children: ReactNode; role: "admin" }) {
  const currentUser = useCurrentUser();
  const location = useLocation();

  if (currentUser.isLoading) {
    return (
      <main className="panel">
        <p>Loading...</p>
      </main>
    );
  }

  if (currentUser.error instanceof ApiError && currentUser.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (currentUser.error) {
    return (
      <main className="panel">
        <h1>Unable to load user</h1>
        <p>{currentUser.error.message}</p>
      </main>
    );
  }

  if (currentUser.data?.role !== role) {
    return <ForbiddenPage message="This admin UI is available only to admin users." />;
  }

  return <>{children}</>;
}
