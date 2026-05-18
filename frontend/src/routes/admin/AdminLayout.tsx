import { Navigate, Route, Routes } from "react-router-dom";
import { AdminSidebar } from "../../components/admin/AdminSidebar";
import { RoleGuard } from "../../features/auth/RoleGuard";
import { DocumentDetailPage } from "./documents/DocumentDetailPage";
import { DocumentListPage } from "./documents/DocumentListPage";
import { DocumentReviewPage } from "./documents/DocumentReviewPage";
import { VersionDetailPage } from "./documents/VersionDetailPage";
import { JobDetailPage } from "./jobs/JobDetailPage";
import { JobListPage } from "./jobs/JobListPage";

export function AdminLayout() {
  return (
    <RoleGuard role="admin">
      <div className="admin-layout">
        <AdminSidebar />
        <Routes>
          <Route index element={<Navigate to="documents" replace />} />
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
      </div>
    </RoleGuard>
  );
}
