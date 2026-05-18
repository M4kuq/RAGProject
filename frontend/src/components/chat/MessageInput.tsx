import { FormEvent } from "react";

export function MessageInput({
  disabled,
  disabledReason,
  isSending,
  onChange,
  onSubmit,
  value
}: {
  disabled: boolean;
  disabledReason: string | null;
  isSending: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  value: string;
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <form onSubmit={submit} className="composer">
      <textarea
        aria-label="message"
        disabled={disabled || isSending}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
      {disabledReason ? <p className="notice">{disabledReason}</p> : null}
      <button disabled={disabled || isSending || value.trim().length === 0} type="submit">
        {isSending ? "送信中" : "Send"}
      </button>
    </form>
  );
}
