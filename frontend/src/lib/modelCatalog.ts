export type ModelOption = {
  label: string;
  modelId?: string;
  value: string;
};

export const DEFAULT_MODEL = "lmstudio:qwen3.5-9b";

export const NVIDIA_RECOMMENDED_MODEL_ID =
  "nvidia/llama-3.3-nemotron-super-49b-v1.5";
export const NVIDIA_RECOMMENDED_MODEL_KEY =
  `nvidia:${NVIDIA_RECOMMENDED_MODEL_ID}`;

const LEGACY_SLOW_NVIDIA_MODEL_KEY =
  "nvidia:meta/llama-3.3-70b-instruct";

const BASE_MODEL_OPTIONS: ModelOption[] = [
  { value: DEFAULT_MODEL, label: "Local Qwen3.5" },
  { value: "openai:gpt-5.5", label: "GPT 5.5" },
  { value: "openai:gpt-5.4", label: "GPT 5.4" },
  { value: "anthropic:claude-sonnet-4-20250514", label: "Claude" },
  { value: "gemini:gemini-2.5-flash", label: "Gemini" }
];

export const NVIDIA_MODEL_OPTIONS: ModelOption[] = [
  {
    label: "NVIDIA Nemotron Super 49B (fast, recommended, external)",
    modelId: NVIDIA_RECOMMENDED_MODEL_ID,
    value: NVIDIA_RECOMMENDED_MODEL_KEY
  }
];

export const NVIDIA_EXTERNAL_DATA_WARNING =
  "NVIDIA\u5916\u90e8API\u3078\u8cea\u554f\u6587\u3068\u53d6\u5f97\u30b3\u30f3\u30c6\u30ad\u30b9\u30c8\u304c\u9001\u4fe1\u3055\u308c\u307e\u3059\u3002\u516c\u958b\u30fb\u30c7\u30e2\u6587\u66f8\u3060\u3051\u3092\u4f7f\u7528\u3057\u3066\u304f\u3060\u3055\u3044\u3002";

export function isNvidiaApiEnabled(
  value: string | boolean | undefined = import.meta.env.VITE_ENABLE_NVIDIA_API
): boolean {
  return value === true || value === "true";
}

export function buildChatModelOptions(
  nvidiaEnabled: boolean = isNvidiaApiEnabled()
): ModelOption[] {
  return nvidiaEnabled
    ? [...BASE_MODEL_OPTIONS, ...NVIDIA_MODEL_OPTIONS]
    : [...BASE_MODEL_OPTIONS];
}

export function isNvidiaModelKey(modelKey: string): boolean {
  return modelKey.startsWith("nvidia:");
}

export function resolveSavedChatModel(
  savedModel: string | null,
  nvidiaEnabled: boolean = isNvidiaApiEnabled()
): string {
  const modelOptions = buildChatModelOptions(nvidiaEnabled);
  if (modelOptions.some((option) => option.value === savedModel)) {
    return savedModel ?? DEFAULT_MODEL;
  }
  if (nvidiaEnabled && savedModel === LEGACY_SLOW_NVIDIA_MODEL_KEY) {
    return NVIDIA_RECOMMENDED_MODEL_KEY;
  }
  return DEFAULT_MODEL;
}

export function nvidiaModelIds(): string[] {
  return NVIDIA_MODEL_OPTIONS.flatMap((option) =>
    option.modelId ? [option.modelId] : []
  );
}

