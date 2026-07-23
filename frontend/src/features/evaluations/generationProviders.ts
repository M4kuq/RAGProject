import { isNvidiaApiEnabled } from "../../lib/modelCatalog";
import type { EvaluationGenerationProvider } from "./evaluationTypes";

const BASE_GENERATION_PROVIDERS: EvaluationGenerationProvider[] = [
  "fake",
  "ollama",
  "lmstudio",
  "openai",
  "anthropic",
  "gemini"
];

export function buildEvaluationGenerationProviders(
  nvidiaEnabled: boolean = isNvidiaApiEnabled()
): EvaluationGenerationProvider[] {
  return nvidiaEnabled
    ? [...BASE_GENERATION_PROVIDERS, "nvidia"]
    : [...BASE_GENERATION_PROVIDERS];
}

