import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { useCurrentUser } from "../features/auth/authHooks";
import { ChatPage } from "../routes/ChatPage";
import { LoginPage } from "../routes/LoginPage";
import { SettingsPage } from "../routes/SettingsPage";
import { AdminLayout } from "../routes/admin/AdminLayout";

function userInitial(displayName: string | null): string {
  const normalized = (displayName ?? "").trim();
  return normalized ? normalized.slice(0, 1).toUpperCase() : "U";
}

function TopNav() {
  const currentUser = useCurrentUser();
  const displayName = currentUser.data?.display_name ?? null;

  return (
    <nav className="topnav">
      <Link to="/chat">Chat</Link>
      {currentUser.data ? (
        <Link aria-label="User settings" className="user-settings-button" title="User settings" to="/settings">
          {userInitial(displayName)}
        </Link>
      ) : null}
    </nav>
  );
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <div className="shell">
        <TopNav />
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/chat/temp/:temporaryChatId?" element={<ChatPage mode="temporary" />} />
          <Route path="/chat/:chatSessionId?" element={<ChatPage mode="active" />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/admin/*" element={<AdminLayout />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
