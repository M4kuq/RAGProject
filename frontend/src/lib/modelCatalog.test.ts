import { describe, expect, test } from "vitest";
import {
  buildChatModelOptions,
  isNvidiaApiEnabled,
  isNvidiaModelKey,
  NVIDIA_EXTERNAL_DATA_WARNING,
  NVIDIA_MODEL_OPTIONS,
  NVIDIA_RECOMMENDED_MODEL_KEY,
  nvidiaModelIds,
  resolveSavedChatModel
} from "./modelCatalog";

describe("NVIDIA local model catalog", () => {
  test("shows the recommended NVIDIA model only when the feature flag is enabled", () => {
    const enabled = buildChatModelOptions(true);
    const disabled = buildChatModelOptions(false);

    expect(enabled.filter((option) => option.value.startsWith("nvidia:"))).toEqual(
      NVIDIA_MODEL_OPTIONS
    );
    expect(disabled.some((option) => option.value.startsWith("nvidia:"))).toBe(false);
  });

  test("treats only the literal true value as enabled", () => {
    expect(isNvidiaApiEnabled("true")).toBe(true);
    expect(isNvidiaApiEnabled(true)).toBe(true);
    expect(isNvidiaApiEnabled("false")).toBe(false);
    expect(isNvidiaApiEnabled(undefined)).toBe(false);
  });

  test("provides catalog model IDs for chat and evaluation inputs", () => {
    expect(nvidiaModelIds()).toEqual([
      "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    ]);
    expect(isNvidiaModelKey(NVIDIA_RECOMMENDED_MODEL_KEY)).toBe(true);
    expect(isNvidiaModelKey("openai:gpt-5.5")).toBe(false);
  });

  test("migrates the removed slow Llama selection to the recommended model", () => {
    expect(
      resolveSavedChatModel("nvidia:meta/llama-3.3-70b-instruct", true)
    ).toBe(NVIDIA_RECOMMENDED_MODEL_KEY);
    expect(
      resolveSavedChatModel("nvidia:meta/llama-3.3-70b-instruct", false)
    ).toBe("lmstudio:qwen3.5-9b");
  });

  test("warns that retrieved context is sent to an external API", () => {
    expect(NVIDIA_EXTERNAL_DATA_WARNING).toContain("NVIDIA");
    expect(NVIDIA_EXTERNAL_DATA_WARNING).toContain("\u53d6\u5f97\u30b3\u30f3\u30c6\u30ad\u30b9\u30c8");
    expect(NVIDIA_EXTERNAL_DATA_WARNING).toContain("\u516c\u958b\u30fb\u30c7\u30e2\u6587\u66f8");
  });
});

