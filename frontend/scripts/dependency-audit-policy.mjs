import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";

const SEVERITIES = ["info", "low", "moderate", "high", "critical"];
const POLICY_PATH = new URL("../security/dependency-audit-policy.json", import.meta.url);

export class AuditGateError extends Error {
  constructor(reason, details = {}) {
    super(reason);
    this.name = "AuditGateError";
    this.reason = reason;
    this.details = details;
  }
}

export function loadPolicy() {
  const policy = JSON.parse(readFileSync(POLICY_PATH, "utf8"));
  if (
    policy.schemaVersion !== 1 ||
    typeof policy.reviewDeadline !== "string" ||
    !policy.knownDebt
  ) {
    throw new AuditGateError("policy_malformed");
  }
  return policy;
}

function severityCounts(report) {
  const counts = report?.metadata?.vulnerabilities;
  if (!counts || typeof counts !== "object") {
    throw new AuditGateError("malformed_output");
  }
  const normalized = {};
  for (const severity of SEVERITIES) {
    const value = counts[severity];
    if (!Number.isInteger(value) || value < 0) {
      throw new AuditGateError("malformed_output");
    }
    normalized[severity] = value;
  }
  return normalized;
}

function vulnerabilities(report) {
  if (
    report?.auditReportVersion !== 2 ||
    !report.vulnerabilities ||
    typeof report.vulnerabilities !== "object" ||
    Array.isArray(report.vulnerabilities)
  ) {
    throw new AuditGateError("malformed_output");
  }
  return report.vulnerabilities;
}

function advisoryIds(vulnerability) {
  if (!Array.isArray(vulnerability.via)) {
    throw new AuditGateError("malformed_output");
  }
  const ids = new Set();
  for (const via of vulnerability.via) {
    if (typeof via === "string") {
      continue;
    }
    if (!via || typeof via !== "object" || typeof via.url !== "string") {
      throw new AuditGateError("malformed_output");
    }
    const match = via.url.match(/\/advisories\/(GHSA-[a-z0-9-]+)$/i);
    if (!match) {
      throw new AuditGateError("advisory_id_malformed");
    }
    ids.add(match[1].toUpperCase());
  }
  return [...ids].sort();
}

function totalFindings(counts) {
  return SEVERITIES.reduce((total, severity) => total + counts[severity], 0);
}

function validateReportShape(report) {
  const counts = severityCounts(report);
  const entries = vulnerabilities(report);
  if (Object.keys(entries).length !== totalFindings(counts)) {
    throw new AuditGateError("count_mismatch");
  }
  return { counts, entries };
}

function deadlineEndUtc(reviewDeadline) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(reviewDeadline)) {
    throw new AuditGateError("policy_malformed");
  }
  const parsed = new Date(`${reviewDeadline}T23:59:59.999Z`);
  if (Number.isNaN(parsed.getTime())) {
    throw new AuditGateError("policy_malformed");
  }
  return parsed;
}

export function evaluateAuditReports({
  productionReport,
  fullReport,
  policy,
  now = new Date()
}) {
  const production = validateReportShape(productionReport);
  if (totalFindings(production.counts) !== 0) {
    throw new AuditGateError("production_advisory");
  }

  const full = validateReportShape(fullReport);
  for (const severity of ["moderate", "high", "critical"]) {
    if (full.counts[severity] !== 0) {
      throw new AuditGateError("full_disallowed_severity", { severity });
    }
  }
  if (full.counts.info !== 0) {
    throw new AuditGateError("full_unknown_severity");
  }

  const fullEntries = Object.entries(full.entries);
  if (fullEntries.length === 0) {
    if (full.counts.low !== 0) {
      throw new AuditGateError("count_mismatch");
    }
    return {
      status: "clean",
      productionCounts: production.counts,
      fullCounts: full.counts,
      knownDebt: []
    };
  }

  if (fullEntries.length !== 1 || full.counts.low !== 1) {
    throw new AuditGateError("new_low_advisory");
  }

  const [packageName, vulnerability] = fullEntries[0];
  const debt = policy.knownDebt;
  const ids = advisoryIds(vulnerability);
  if (packageName !== debt.package || vulnerability.name !== debt.package) {
    throw new AuditGateError("package_drift");
  }
  if (vulnerability.severity !== debt.severity || debt.severity !== "low") {
    throw new AuditGateError("severity_drift");
  }
  if (ids.length !== 1 || ids[0] !== debt.advisoryId.toUpperCase()) {
    throw new AuditGateError("advisory_drift");
  }
  if (vulnerability.fixAvailable !== false || debt.fixAvailable !== false) {
    throw new AuditGateError("fix_available");
  }
  if (vulnerability.isDirect !== false || debt.dependencyClass !== "dev") {
    throw new AuditGateError("dependency_class_drift");
  }
  if (production.entries[packageName]) {
    throw new AuditGateError("dependency_became_production");
  }
  if (now > deadlineEndUtc(policy.reviewDeadline)) {
    throw new AuditGateError("tracked_debt_expired");
  }

  return {
    status: "pass_with_known_debt",
    productionCounts: production.counts,
    fullCounts: full.counts,
    knownDebt: [
      {
        advisoryId: debt.advisoryId,
        package: debt.package,
        severity: debt.severity,
        dependencyClass: debt.dependencyClass,
        reviewDeadline: policy.reviewDeadline
      }
    ]
  };
}

export function parseAuditCommandResult(result) {
  if (result.error || ![0, 1].includes(result.status)) {
    throw new AuditGateError("audit_unavailable");
  }
  let report;
  try {
    report = JSON.parse(result.stdout);
  } catch {
    throw new AuditGateError("malformed_output");
  }
  if (report?.error || report?.auditReportVersion !== 2) {
    throw new AuditGateError("audit_unavailable");
  }
  validateReportShape(report);
  return report;
}

function npmInvocation(args) {
  if (process.env.npm_execpath) {
    return {
      command: process.execPath,
      args: [process.env.npm_execpath, ...args]
    };
  }
  return {
    command: process.platform === "win32" ? "npm.cmd" : "npm",
    args
  };
}

export async function runAuditWithRetry(extraArgs) {
  let lastReason = "audit_unavailable";
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    const invocation = npmInvocation(["audit", "--json", ...extraArgs]);
    const result = spawnSync(invocation.command, invocation.args, {
      cwd: new URL("..", import.meta.url),
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
      env: {
        ...process.env,
        NO_UPDATE_NOTIFIER: "1"
      }
    });
    try {
      return parseAuditCommandResult(result);
    } catch (error) {
      if (!(error instanceof AuditGateError)) {
        throw error;
      }
      lastReason = error.reason;
      if (!["audit_unavailable", "malformed_output"].includes(lastReason)) {
        throw error;
      }
      if (attempt === 1) {
        console.log(`DEPENDENCY_AUDIT_RETRY reason=${lastReason} attempt=1`);
        await delay(1000);
      }
    }
  }
  throw new AuditGateError(lastReason);
}

export function formatEvaluationResult(result) {
  const counts = (value) =>
    SEVERITIES.map((severity) => `${severity}=${value[severity]}`).join(" ");
  const lines = [
    `DEPENDENCY_AUDIT_PRODUCTION_PASS ${counts(result.productionCounts)}`,
    `DEPENDENCY_AUDIT_FULL_PASS status=${result.status} ${counts(result.fullCounts)}`
  ];
  for (const debt of result.knownDebt) {
    lines.push(
      [
        "DEPENDENCY_AUDIT_TRACKED_DEBT",
        `advisory=${debt.advisoryId}`,
        `package=${debt.package}`,
        `severity=${debt.severity}`,
        `class=${debt.dependencyClass}`,
        `review_deadline=${debt.reviewDeadline}`
      ].join(" ")
    );
  }
  return lines.join("\n");
}

export async function runGate() {
  const policy = loadPolicy();
  const productionReport = await runAuditWithRetry(["--omit=dev"]);
  const fullReport = await runAuditWithRetry([]);
  return evaluateAuditReports({ productionReport, fullReport, policy });
}
