import { expect, test } from "vitest";
import { buildEvaluationGenerationProviders } from "./generationProviders";

test("adds the NVIDIA evaluation provider only when locally enabled", () => {
  expect(buildEvaluationGenerationProviders(true)).toContain("nvidia");
  expect(buildEvaluationGenerationProviders(false)).not.toContain("nvidia");
});

test("keeps the existing evaluation providers unchanged when NVIDIA is disabled", () => {
  expect(buildEvaluationGenerationProviders(false)).toEqual([
    "fake",
    "ollama",
    "lmstudio",
    "openai",
    "anthropic",
    "gemini"
  ]);
});
