import { FormEvent, KeyboardEvent } from "react";
import type { RagStrategy } from "../../features/chat/chatTypes";

export function MessageInput({
  disabled,
  disabledReason,
  isSending,
  modelOptions,
  onChange,
  onModelChange,
  onStrategyChange,
  onSubmit,
  selectedStrategy,
  selectedModel,
  strategyOptions,
  value
}: {
  disabled: boolean;
  disabledReason: string | null;
  isSending: boolean;
  modelOptions: { label: string; value: string }[];
  onChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onStrategyChange: (value: RagStrategy) => void;
  onSubmit: () => void;
  selectedStrategy: RagStrategy;
  selectedModel: string;
  strategyOptions: { description: string; label: string; value: RagStrategy }[];
  value: string;
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  function submitFromKeyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  const selectedStrategyDescription =
    strategyOptions.find((option) => option.value === selectedStrategy)?.description ?? null;

  return (
    <form onSubmit={submit} className="composer">
      <textarea
        aria-label="message"
        disabled={disabled || isSending}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={submitFromKeyboard}
        placeholder="Ask anything about your indexed documents..."
        rows={1}
        value={value}
      />
      <div className="composer-footer">
        <div className="composer-guidance">
          {disabledReason ? (
            <p className="notice">{disabledReason}</p>
          ) : (
            <p className="notice">Enter to send, Shift+Enter for a new line.</p>
          )}
          {selectedStrategyDescription ? (
            <p className="strategy-description">{selectedStrategyDescription}</p>
          ) : null}
        </div>
        <div className="composer-controls">
          <select
            aria-label="rag strategy"
            className="model-select"
            disabled={isSending}
            onChange={(event) => onStrategyChange(event.target.value as RagStrategy)}
            value={selectedStrategy}
          >
            {strategyOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            aria-label="model"
            className="model-select"
            disabled={isSending}
            onChange={(event) => onModelChange(event.target.value)}
            value={selectedModel}
          >
            {modelOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button disabled={disabled || isSending || value.trim().length === 0} type="submit">
            {isSending ? "Sending..." : "Send"}
          </button>
        </div>
      </div>
    </form>
  );
}
