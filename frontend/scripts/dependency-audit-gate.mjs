import { AuditGateError, formatEvaluationResult, runGate } from "./dependency-audit-policy.mjs";

try {
  const result = await runGate();
  console.log(formatEvaluationResult(result));
} catch (error) {
  const reason = error instanceof AuditGateError ? error.reason : "internal_error";
  console.error(`DEPENDENCY_AUDIT_FAIL reason=${reason}`);
  process.exitCode = 1;
}
