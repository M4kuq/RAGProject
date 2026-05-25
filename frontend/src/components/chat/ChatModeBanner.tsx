import { ChatMode } from "../../features/chat/chatTypes";

export function ChatModeBanner({ mode }: { mode: ChatMode }) {
  if (mode === "active") {
    return null;
  }
  const message =
    mode === "archived"
      ? "This chat is archived and can only be read."
      : mode === "temporary_expired"
        ? "This temporary chat has expired and can only be read."
        : "This is a temporary chat.";
  return (
    <div className={`mode-banner mode-${mode}`} role="status">
      {message}
    </div>
  );
}
