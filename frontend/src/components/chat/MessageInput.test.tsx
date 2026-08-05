import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { NVIDIA_EXTERNAL_DATA_WARNING } from "../../lib/modelCatalog";
import { MessageInput } from "./MessageInput";

const baseProps = {
  disabled: false,
  disabledReason: null,
  externalDataConsent: false,
  externalDataConsentRequired: true,
  isSending: false,
  modelOptions: [
    {
      label: "NVIDIA Nemotron Super 49B (fast, recommended, external)",
      value: "nvidia:nvidia/llama-3.3-nemotron-super-49b-v1.5"
    }
  ],
  onChange: vi.fn(),
  onExternalDataConsentChange: vi.fn(),
  onModelChange: vi.fn(),
  onStrategyChange: vi.fn(),
  onSubmit: vi.fn(),
  selectedStrategy: "dense" as const,
  selectedModel: "nvidia:nvidia/llama-3.3-nemotron-super-49b-v1.5",
  strategyOptions: [
    {
      description: "Dense retrieval",
      label: "Dense",
      value: "dense" as const
    }
  ],
  value: ""
};

test("shows the external data warning for an NVIDIA selection", () => {
  render(<MessageInput {...baseProps} externalDataWarning={NVIDIA_EXTERNAL_DATA_WARNING} />);

  expect(screen.getByRole("status")).toHaveTextContent(NVIDIA_EXTERNAL_DATA_WARNING);
});

test("does not render an external data warning for local models", () => {
  render(
    <MessageInput
      {...baseProps}
      externalDataConsentRequired={false}
      externalDataWarning={null}
      selectedModel="lmstudio:qwen3.5-9b"
    />
  );

  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("allow external model transfer for this request")
  ).not.toBeInTheDocument();
});

test("requires an explicit checkbox for an external request", () => {
  const onExternalDataConsentChange = vi.fn();
  render(
    <MessageInput
      {...baseProps}
      externalDataWarning={NVIDIA_EXTERNAL_DATA_WARNING}
      onExternalDataConsentChange={onExternalDataConsentChange}
      value="What is RAG?"
    />
  );

  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("allow external model transfer for this request"));
  expect(onExternalDataConsentChange).toHaveBeenCalledWith(true);
});
