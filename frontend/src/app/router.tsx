import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { useCurrentUser } from "../features/auth/authHooks";
import { ChatPage } from "../routes/ChatPage";
import { LoginPage } from "../routes/LoginPage";
import { SettingsPage } from "../routes/SettingsPage";
import { AdminLayout } from "../routes/admin/AdminLayout";

function TopNav() {
  const currentUser = useCurrentUser();
  const isAdmin = currentUser.data?.role === "admin";

  return (
    <nav className="topnav">
      <Link to="/chat">Chat</Link>
      <Link to="/settings">Settings</Link>
      {isAdmin ? <Link to="/admin/documents">Admin</Link> : null}
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
