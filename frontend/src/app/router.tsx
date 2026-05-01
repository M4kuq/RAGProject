import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { AdminPage } from "../routes/AdminPage";
import { ChatPage } from "../routes/ChatPage";
import { LoginPage } from "../routes/LoginPage";
import { SettingsPage } from "../routes/SettingsPage";

export function AppRouter() {
  return (
    <BrowserRouter>
      <div className="shell">
        <nav className="topnav">
          <Link to="/chat">Chat</Link>
          <Link to="/settings">Settings</Link>
          <Link to="/admin">Admin</Link>
        </nav>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/chat/temp/:temporaryChatId?" element={<ChatPage mode="temporary" />} />
          <Route path="/chat/:chatSessionId?" element={<ChatPage mode="active" />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/admin/*" element={<AdminPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
