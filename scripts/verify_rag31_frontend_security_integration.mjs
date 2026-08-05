import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const REPOSITORY_ROOT = fileURLToPath(new URL("..", import.meta.url));
const MANIFEST_PATH = new URL(
  "../docs/security/rag31_frontend_security_integration_manifest.json",
  import.meta.url
);

class IntegrationError extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

function fail(reason) {
  throw new IntegrationError(reason);
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

function verifyFileSet(actual, expected, reason) {
  if (actual.length !== expected.count || sha256Lines(actual) !== expected.sha256) {
    fail(reason);
  }
  if (expected.files && JSON.stringify(actual) !== JSON.stringify(expected.files)) {
    fail(reason);
  }
}

function readText(relativePath) {
  return readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

function alembicHeads() {
  const directory = new URL("../backend/alembic/versions/", import.meta.url);
  const revisions = new Set();
  const parents = new Set();
  for (const filename of readdirSync(directory).filter((name) => name.endsWith(".py"))) {
    const source = readFileSync(new URL(filename, directory), "utf8");
    const revision = source.match(
      /^revision(?:\s*:\s*[^=]+)?\s*=\s*["']([^"']+)["']/m
    )?.[1];
    if (!revision) fail("alembic_revision_malformed");
    revisions.add(revision);

    const assignment = source.match(
      /^down_revision(?:\s*:\s*[^=]+)?\s*=\s*([\s\S]*?)(?=^branch_labels|^depends_on)/m
    )?.[1];
    if (assignment === undefined) fail("alembic_down_revision_malformed");
    for (const match of assignment.matchAll(/["']([^"']+)["']/g)) {
      parents.add(match[1]);
    }
  }
  return [...revisions].filter((revision) => !parents.has(revision)).sort();
}

function verifySemanticComposition(manifest) {
  const chatPage = readText("frontend/src/routes/ChatPage.tsx");
  const chatTest = readText("frontend/src/routes/ChatPage.test.tsx");
  const loginPage = readText("frontend/src/routes/LoginPage.tsx");
  const modelCatalog = readText("frontend/src/lib/modelCatalog.ts");
  const backendConfig = readText("backend/app/core/config.py");
  const packageJson = JSON.parse(readText("frontend/package.json"));

  if (!chatPage.includes('from "react-router"') || chatPage.includes("react-router-dom")) {
    fail("chat_router_v8_missing");
  }
  if (
    !chatPage.includes("external_model_egress_consent: consentForRequest") ||
    !chatPage.includes("externalDataConsentRequired") ||
    !chatTest.includes("external_model_egress_consent: true")
  ) {
    fail("chat_egress_consent_missing");
  }
  if (
    !loginPage.includes("function isSafeLocalPathname") ||
    !loginPage.includes('pathname.startsWith("//")') ||
    !loginPage.includes('pathname.includes("\\\\")')
  ) {
    fail("same_origin_redirect_gate_missing");
  }
  if (
    !modelCatalog.includes("VITE_ENABLE_EXTERNAL_MODEL_SELECTION") ||
    !modelCatalog.includes('export const DEFAULT_MODEL = "lmstudio:qwen3.5-9b"')
  ) {
    fail("server_owned_model_catalog_missing");
  }
  if (
    !backendConfig.includes("external_model_egress_policy: Literal[\"deny\", \"mask\", \"allow\"] = \"deny\"") ||
    !backendConfig.includes("rag_user_model_selection_enabled: bool = False") ||
    !backendConfig.includes("qwen_cascade_enabled: bool = False")
  ) {
    fail("backend_default_deny_drift");
  }
  if (
    packageJson.dependencies?.react !== manifest.runtime.react ||
    packageJson.dependencies?.["react-router"] !== manifest.runtime.reactRouter ||
    packageJson.dependencies?.["react-router-dom"] !== undefined
  ) {
    fail("frontend_runtime_drift");
  }
}

function verifyRuntimePins(manifest) {
  const packageJson = JSON.parse(readText("frontend/package.json"));
  const dockerfile = readText("frontend/Dockerfile");
  const frontendWorkflow = readText(".github/workflows/frontend-ci.yml");
  const secretWorkflow = readText(".github/workflows/secret-scan.yml");
  const secretGate = readText("scripts/secret_scan_gate.py");

  if (packageJson.engines?.node !== `>=${manifest.runtime.nodeMinimum}`) {
    fail("node_contract_drift");
  }
  if (packageJson.packageManager !== manifest.runtime.npmPackageManager) {
    fail("npm_contract_drift");
  }
  if (!dockerfile.includes(`FROM node:${manifest.runtime.nodeMinimum}-alpine`)) {
    fail("docker_node_contract_drift");
  }
  if (!frontendWorkflow.includes(`node-version: "${manifest.runtime.nodeMinimum}"`)) {
    fail("ci_node_contract_drift");
  }
  if (!frontendWorkflow.includes("verify_rag31_frontend_security_integration.mjs")) {
    fail("cross_canonical_workflow_missing");
  }
  if (!secretWorkflow.includes("fetch-depth: 0")) fail("secret_history_missing");
  if (!secretWorkflow.includes(manifest.runtime.gitleaksImageDigest)) {
    fail("gitleaks_digest_drift");
  }
  if (!secretGate.includes(`EXPECTED_VERSION = "${manifest.runtime.gitleaks}"`)) {
    fail("gitleaks_version_drift");
  }
}

function verify() {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));
  if (manifest.schemaVersion !== 1) fail("manifest_schema");

  const head = git("rev-parse", "HEAD");
  for (const source of Object.values(manifest.sources)) {
    if (!isAncestor(source, head)) fail("source_ancestry_missing");
  }
  if (!isAncestor(manifest.sources.pr141, manifest.sources.pr145)) {
    fail("pr141_not_in_pr145");
  }

  const parents = git("show", "-s", "--format=%P", manifest.integrationMerge).split(" ");
  if (JSON.stringify(parents) !== JSON.stringify(manifest.mergeParents)) {
    fail("merge_parent_drift");
  }
  if (git("show", "-s", "--format=%T", manifest.integrationMerge) !== manifest.integrationMergeTree) {
    fail("merge_tree_drift");
  }
  for (const [source, expectedTree] of Object.entries(manifest.sourceTrees)) {
    if (git("show", "-s", "--format=%T", manifest.sources[source]) !== expectedTree) {
      fail("source_tree_drift");
    }
  }

  const pr145Files = changedFiles(manifest.sourceBases.pr145, manifest.sources.pr145);
  const pr149Files = changedFiles(manifest.sourceBases.pr149, manifest.sources.pr149);
  verifyFileSet(pr145Files, manifest.sourceFiles.pr145, "pr145_file_set_drift");
  verifyFileSet(pr149Files, manifest.sourceFiles.pr149, "pr149_file_set_drift");

  const intersection = pr145Files.filter((file) => pr149Files.includes(file));
  verifyFileSet(intersection, manifest.sourceFiles.intersection, "source_intersection_drift");
  const union = [...new Set([...pr145Files, ...pr149Files])].sort();
  verifyFileSet(union, manifest.sourceFiles.union, "source_union_drift");

  const integrated = changedFiles(manifest.mainBase, head);
  verifyFileSet(integrated, manifest.integrationFiles.all, "integration_file_set_drift");
  const integratedSet = new Set(integrated);
  if (union.some((file) => !integratedSet.has(file))) fail("integration_missing_files");
  const integrationOnly = integrated.filter((file) => !union.includes(file));
  verifyFileSet(integrationOnly, manifest.integrationFiles.only, "integration_only_drift");

  if (manifest.rollback.codeSha !== manifest.sources.pr145) fail("rollback_sha_drift");
  const heads = alembicHeads();
  if (JSON.stringify(heads) !== JSON.stringify(manifest.migration.heads)) {
    fail("alembic_head_drift");
  }

  verifyRuntimePins(manifest);
  verifySemanticComposition(manifest);

  console.log(
    [
      "RAG31_FRONTEND_SECURITY_INTEGRATION_PASS",
      "sources=3",
      `union=${union.length}`,
      `intersection=${intersection.length}`,
      "missing=0",
      `integration_only=${integrationOnly.length}`,
      `alembic_heads=${heads.length}`,
      `rollback=${manifest.rollback.codeSha}`
    ].join(" ")
  );
}

try {
  verify();
} catch (error) {
  const reason = error instanceof IntegrationError ? error.reason : "internal_error";
  console.error(`RAG31_FRONTEND_SECURITY_INTEGRATION_FAIL reason=${reason}`);
  process.exitCode = 1;
}
