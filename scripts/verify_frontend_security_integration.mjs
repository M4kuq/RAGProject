import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const REPOSITORY_ROOT = fileURLToPath(new URL("..", import.meta.url));
const MANIFEST_PATH = new URL(
  "../docs/security/frontend_security_integration_manifest.json",
  import.meta.url
);

class ManifestError extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

function fail(reason) {
  throw new ManifestError(reason);
}

function git(...args) {
  return execFileSync("git", args, {
    cwd: REPOSITORY_ROOT,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"]
  }).trim();
}

function isAncestor(ancestor, descendant) {
  return (
    spawnSync("git", ["merge-base", "--is-ancestor", ancestor, descendant], {
      cwd: REPOSITORY_ROOT,
      stdio: "ignore"
    }).status === 0
  );
}

function changedFiles(base, head) {
  const output = git("diff", "--name-only", `${base}..${head}`);
  return output ? output.split(/\r?\n/).sort() : [];
}

function sha256Lines(lines) {
  return createHash("sha256").update(lines.join("\n"), "utf8").digest("hex");
}

function parseVersion(value) {
  const match = value.trim().replace(/^v/, "").match(/^(\d+)\.(\d+)\.(\d+)/);
  if (!match) {
    fail("runtime_version_malformed");
  }
  return match.slice(1).map(Number);
}

function atLeast(actual, minimum) {
  for (let index = 0; index < 3; index += 1) {
    if (actual[index] > minimum[index]) return true;
    if (actual[index] < minimum[index]) return false;
  }
  return true;
}

function readText(relativePath) {
  return readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

function verify() {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));
  if (manifest.schemaVersion !== 1) fail("manifest_schema");

  const head = git("rev-parse", "HEAD");
  const sourceValues = Object.values(manifest.sources);
  for (const source of sourceValues) {
    if (!isAncestor(source, head)) fail("source_ancestry_missing");
  }
  if (!isAncestor(manifest.sources.pr146, manifest.sources.pr147)) {
    fail("pr146_not_in_pr147");
  }

  const parents = git("show", "-s", "--format=%P", manifest.integrationMerge).split(" ");
  if (JSON.stringify(parents) !== JSON.stringify(manifest.mergeParents)) {
    fail("merge_parent_drift");
  }
  const mergeTree = git("show", "-s", "--format=%T", manifest.integrationMerge);
  if (mergeTree !== manifest.integrationMergeTree) fail("merge_tree_drift");
  for (const [name, expectedTree] of Object.entries(manifest.sourceTrees)) {
    const actualTree = git("show", "-s", "--format=%T", manifest.sources[name]);
    if (actualTree !== expectedTree) fail("source_tree_drift");
  }

  const pr147Files = changedFiles(manifest.mainBase, manifest.sources.pr147);
  const pr148Files = changedFiles(manifest.mainBase, manifest.sources.pr148);
  const pr146Files = new Set(changedFiles(manifest.mainBase, manifest.sources.pr146));
  if ([...pr146Files].some((file) => !pr147Files.includes(file))) {
    fail("pr146_union_missing");
  }
  const intersection = pr147Files.filter((file) => pr148Files.includes(file));
  if (intersection.length !== manifest.sourceIntersection) fail("source_intersection_drift");
  const union = [...new Set([...pr147Files, ...pr148Files])].sort();
  if (
    union.length !== manifest.sourceUnion.count ||
    sha256Lines(union) !== manifest.sourceUnion.sha256
  ) {
    fail("source_union_drift");
  }
  const integratedFiles = new Set(changedFiles(manifest.mainBase, head));
  const missing = union.filter((file) => !integratedFiles.has(file));
  if (missing.length !== 0) fail("integration_missing_files");

  if (manifest.rollbackSha !== manifest.sources.pr147) fail("rollback_sha_drift");

  const packageJson = JSON.parse(readText("frontend/package.json"));
  const packageLock = JSON.parse(readText("frontend/package-lock.json"));
  if (packageJson.engines?.node !== `>=${manifest.runtime.nodeMinimum}`) {
    fail("node_contract_drift");
  }
  if (packageJson.packageManager !== manifest.runtime.npmPackageManager) {
    fail("npm_contract_drift");
  }
  if (packageLock.packages?.[""]?.engines?.node !== packageJson.engines.node) {
    fail("lockfile_node_contract_drift");
  }
  if (packageJson.overrides?.["@babel/core"] !== manifest.runtime.babelCoreOverride) {
    fail("babel_override_drift");
  }
  if (
    packageLock.packages?.["node_modules/@babel/core"]?.version !==
    manifest.runtime.babelCoreOverride
  ) {
    fail("babel_lockfile_drift");
  }

  const nodeVersion = parseVersion(process.version);
  const minimumNode = parseVersion(manifest.runtime.nodeMinimum);
  if (!atLeast(nodeVersion, minimumNode)) fail("node_runtime_too_old");
  const npmCommand = process.env.npm_execpath ? process.execPath : "npm";
  const npmArgs = process.env.npm_execpath
    ? [process.env.npm_execpath, "--version"]
    : ["--version"];
  const npmResult = spawnSync(npmCommand, npmArgs, {
    cwd: REPOSITORY_ROOT,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"]
  });
  if (npmResult.status !== 0) fail("npm_runtime_unavailable");
  const npmVersion = parseVersion(npmResult.stdout);
  if (npmVersion[0] !== manifest.runtime.npmMajor) fail("npm_runtime_major_drift");

  const dockerfile = readText("frontend/Dockerfile");
  const frontendWorkflow = readText(".github/workflows/frontend-ci.yml");
  const secretWorkflow = readText(".github/workflows/secret-scan.yml");
  const secretGate = readText("scripts/secret_scan_gate.py");
  if (!dockerfile.includes("FROM node:22.22-alpine")) fail("docker_node_contract_drift");
  if (!frontendWorkflow.includes('node-version: "22.22"')) fail("ci_node_contract_drift");
  if (!frontendWorkflow.includes("npm run security:audit")) fail("audit_workflow_missing");
  if (!secretWorkflow.includes("fetch-depth: 0")) fail("secret_history_missing");
  if (!secretWorkflow.includes(manifest.runtime.gitleaksImageDigest)) {
    fail("gitleaks_digest_drift");
  }
  if (!secretGate.includes(`EXPECTED_VERSION = "${manifest.runtime.gitleaks}"`)) {
    fail("gitleaks_version_drift");
  }

  const dependencyPolicy = JSON.parse(
    readText("frontend/security/dependency-audit-policy.json")
  );
  if (dependencyPolicy.reviewDeadline !== manifest.dependencyDebtReviewDeadline) {
    fail("dependency_deadline_drift");
  }

  const extras = [...integratedFiles].filter((file) => !union.includes(file)).length;
  console.log(
    [
      "FRONTEND_SECURITY_MANIFEST_PASS",
      "sources=3",
      `union=${union.length}`,
      `intersection=${intersection.length}`,
      "missing=0",
      `integration_extras=${extras}`,
      `node=${nodeVersion.join(".")}`,
      `npm_major=${npmVersion[0]}`,
      `gitleaks=${manifest.runtime.gitleaks}`,
      `rollback=${manifest.rollbackSha}`
    ].join(" ")
  );
}

try {
  verify();
} catch (error) {
  const reason = error instanceof ManifestError ? error.reason : "internal_error";
  console.error(`FRONTEND_SECURITY_MANIFEST_FAIL reason=${reason}`);
  process.exitCode = 1;
}
