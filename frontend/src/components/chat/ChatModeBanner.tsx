import { ChatMode } from "../../features/chat/chatTypes";

export function ChatModeBanner({ mode }: { mode: ChatMode }) {
  if (mode === "active") {
    return null;
  }
  const message =
    mode === "archived"
      ? "アーカイブ済みのため読み取り専用です。"
      : mode === "temporary_expired"
        ? "一時チャットの期限が切れたため読み取り専用です。"
        : "一時チャットです。期限までは送信できます。";
  return (
    <div className={`mode-banner mode-${mode}`} role="status">
      {message}
    </div>
  );
}
