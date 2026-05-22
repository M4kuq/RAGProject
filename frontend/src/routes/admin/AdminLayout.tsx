import { Route, Routes } from "react-router-dom";
import { AdminSidebar } from "../../components/admin/AdminSidebar";
import { ErrorState, LoadingState } from "../../components/common/States";
import { RoleGuard } from "../../features/auth/RoleGuard";
import { useCsrfToken } from "../../features/auth/authHooks";
import { AdminPage } from "../AdminPage";
import { DocumentDetailPage } from "./documents/DocumentDetailPage";
import { DocumentListPage } from "./documents/DocumentListPage";
import { DocumentReviewPage } from "./documents/DocumentReviewPage";
import { VersionDetailPage } from "./documents/VersionDetailPage";
import { EvaluationDetailPage } from "./evaluations/EvaluationDetailPage";
import { EvaluationListPage } from "./evaluations/EvaluationListPage";
import { JobDetailPage } from "./jobs/JobDetailPage";
import { JobListPage } from "./jobs/JobListPage";

export function AdminLayout() {
  return (
    <RoleGuard role="admin">
      <AdminShell />
    </RoleGuard>
  );
}

function AdminShell() {
  const csrf = useCsrfToken();

  return (
    <div className="admin-layout">
      <AdminSidebar />
      {csrf.isLoading ? (
        <main className="admin-main">
          <LoadingState label="Loading admin session..." />
        </main>
      ) : null}
      {csrf.isError ? (
        <main className="admin-main">
          <ErrorState title="Unable to load admin session" error={csrf.error} />
        </main>
      ) : null}
      {csrf.isSuccess ? (
        <Routes>
          <Route index element={<AdminPage />} />
          <Route path="evaluations" element={<EvaluationListPage />} />
          <Route path="evaluations/:evaluationRunId" element={<EvaluationDetailPage />} />
          <Route path="documents" element={<DocumentListPage />} />
          <Route path="documents/review" element={<DocumentReviewPage />} />
          <Route path="documents/:logicalDocumentId" element={<DocumentDetailPage />} />
          <Route
            path="documents/:logicalDocumentId/versions/:documentVersionId"
            element={<VersionDetailPage />}
          />
          <Route path="jobs" element={<JobListPage />} />
          <Route path="jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      ) : null}
    </div>
  );
}
