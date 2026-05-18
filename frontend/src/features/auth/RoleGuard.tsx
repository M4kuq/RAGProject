import type { ReactNode } from "react";
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

  if (currentUser.isLoading) {
    return (
      <main className="panel">
        <p>Loading...</p>
      </main>
    );
  }

  if (currentUser.data?.role !== role) {
    return <ForbiddenPage message="This admin UI is available only to admin users." />;
  }

  return <>{children}</>;
}
