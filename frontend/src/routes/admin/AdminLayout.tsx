import { Route, Routes } from "react-router-dom";
import { AdminSidebar } from "../../components/admin/AdminSidebar";
import { RoleGuard } from "../../features/auth/RoleGuard";
import { AdminPage } from "../AdminPage";
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
          <Route index element={<AdminPage />} />
          <Route path="evaluations" element={<AdminPage />} />
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
