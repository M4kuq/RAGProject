import assert from "node:assert/strict";
import test from "node:test";

// Keep this Node-only suite outside Vitest's *.test.* discovery pattern.

import {
  AuditGateError,
  evaluateAuditReports,
  formatEvaluationResult,
  parseAuditCommandResult
} from "./dependency-audit-policy.mjs";

const POLICY = {
  schemaVersion: 1,
  reviewDeadline: "2026-09-04",
  knownDebt: {
    advisoryId: "GHSA-4x5r-pxfx-6jf8",
    package: "@babel/core",
    severity: "low",
    dependencyClass: "dev",
    fixAvailable: false
  }
};
const WITHIN_DEADLINE = new Date("2026-08-05T00:00:00Z");

function emptyReport() {
  return {
    auditReportVersion: 2,
    vulnerabilities: {},
    metadata: {
      vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0, critical: 0 }
    }
  };
}

function reportWith({
  packageName = "@babel/core",
  advisoryId = "GHSA-4x5r-pxfx-6jf8",
  severity = "low",
  fixAvailable = false,
  isDirect = false,
  node = "node_modules/redacted"
} = {}) {
  return {
    auditReportVersion: 2,
    vulnerabilities: {
      [packageName]: {
        name: packageName,
        severity,
        isDirect,
        via: [
          {
            url: `https://github.com/advisories/${advisoryId}`
          }
        ],
        nodes: [node],
        fixAvailable
      }
    },
    metadata: {
      vulnerabilities: {
        info: 0,
        low: severity === "low" ? 1 : 0,
        moderate: severity === "moderate" ? 1 : 0,
        high: severity === "high" ? 1 : 0,
        critical: severity === "critical" ? 1 : 0
      }
    }
  };
}

function expectReason(action, reason) {
  assert.throws(action, (error) => {
    assert.ok(error instanceof AuditGateError);
    assert.equal(error.reason, reason);
    return true;
  });
}

for (const severity of ["low", "moderate", "high", "critical"]) {
  test(`production ${severity} fails closed`, () => {
    expectReason(
      () =>
        evaluateAuditReports({
          productionReport: reportWith({ severity }),
          fullReport: emptyReport(),
          policy: POLICY,
          now: WITHIN_DEADLINE
        }),
      "production_advisory"
    );
  });
}

for (const severity of ["low", "moderate", "high", "critical"]) {
  test(`new dev ${severity} fails closed`, () => {
    const expected = severity === "low" ? "package_drift" : "full_disallowed_severity";
    expectReason(
      () =>
        evaluateAuditReports({
          productionReport: emptyReport(),
          fullReport: reportWith({
            packageName: "@example/new-dev-package",
            advisoryId: "GHSA-aaaa-bbbb-cccc",
            severity
          }),
          policy: POLICY,
          now: WITHIN_DEADLINE
        }),
      expected
    );
  });
}

test("exact Babel Low with no fix and an active review window passes as tracked debt", () => {
  const result = evaluateAuditReports({
    productionReport: emptyReport(),
    fullReport: reportWith(),
    policy: POLICY,
    now: WITHIN_DEADLINE
  });
  assert.equal(result.status, "pass_with_known_debt");
  assert.deepEqual(result.knownDebt, [
    {
      advisoryId: "GHSA-4x5r-pxfx-6jf8",
      package: "@babel/core",
      severity: "low",
      dependencyClass: "dev",
      reviewDeadline: "2026-09-04"
    }
  ]);
});

test("a newly available Babel fix fails", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: emptyReport(),
        fullReport: reportWith({ fixAvailable: true }),
        policy: POLICY,
        now: WITHIN_DEADLINE
      }),
    "fix_available"
  );
});

test("tracked debt expiration fails", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: emptyReport(),
        fullReport: reportWith(),
        policy: POLICY,
        now: new Date("2026-09-05T00:00:00Z")
      }),
    "tracked_debt_expired"
  );
});

test("unknown advisory fails", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: emptyReport(),
        fullReport: reportWith({ advisoryId: "GHSA-aaaa-bbbb-cccc" }),
        policy: POLICY,
        now: WITHIN_DEADLINE
      }),
    "advisory_drift"
  );
});

test("unknown package fails", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: emptyReport(),
        fullReport: reportWith({ packageName: "@example/unknown" }),
        policy: POLICY,
        now: WITHIN_DEADLINE
      }),
    "package_drift"
  );
});

test("severity drift fails", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: emptyReport(),
        fullReport: reportWith({ severity: "moderate" }),
        policy: POLICY,
        now: WITHIN_DEADLINE
      }),
    "full_disallowed_severity"
  );
});

test("the exact Babel advisory fails if it becomes a production dependency", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: reportWith(),
        fullReport: reportWith(),
        policy: POLICY,
        now: WITHIN_DEADLINE
      }),
    "production_advisory"
  );
});

test("direct dependency-class drift fails", () => {
  expectReason(
    () =>
      evaluateAuditReports({
        productionReport: emptyReport(),
        fullReport: reportWith({ isDirect: true }),
        policy: POLICY,
        now: WITHIN_DEADLINE
      }),
    "dependency_class_drift"
  );
});

test("malformed JSON fails closed without echoing the body", () => {
  expectReason(
    () => parseAuditCommandResult({ status: 1, stdout: "{not-json", stderr: "" }),
    "malformed_output"
  );
});

test("network or non-audit error fails closed", () => {
  expectReason(
    () =>
      parseAuditCommandResult({
        status: 1,
        stdout: JSON.stringify({ error: { code: "registry_unavailable" } }),
        stderr: "redacted"
      }),
    "audit_unavailable"
  );
});

test("formatted output excludes dependency paths and raw report fields", () => {
  const report = reportWith({ node: "node_modules/private/user/path" });
  const result = evaluateAuditReports({
    productionReport: emptyReport(),
    fullReport: report,
    policy: POLICY,
    now: WITHIN_DEADLINE
  });
  const output = formatEvaluationResult(result);
  assert.doesNotMatch(output, /node_modules|private\/user|nodes|via|url/);
  assert.match(output, /GHSA-4x5r-pxfx-6jf8/);
});
