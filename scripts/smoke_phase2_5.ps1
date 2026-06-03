param(
  [switch]$Deep,
  [switch]$K8sDryRun,
  [switch]$RunExistingPhase2
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendUrl = if ($env:BACKEND_URL) { $env:BACKEND_URL } else { "http://localhost:8000" }
$FrontendUrl = if ($env:FRONTEND_URL) { $env:FRONTEND_URL } else { "http://localhost:5173" }
$AdminEmail = $env:SMOKE_ADMIN_EMAIL
$AdminPassword = $env:SMOKE_ADMIN_PASSWORD

function Write-Step([string]$Message) {
  Write-Host "[phase2.5-smoke] $Message"
}

function Assert-PathExists([string]$RelativePath) {
  $path = Join-Path $RepoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $path)) {
    throw "required Phase2.5 artifact is missing: $RelativePath"
  }
}

function Invoke-Checked([scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Test-Url([string]$Url) {
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5 | Out-Null
    return $true
  } catch {
    return $false
  }
}

Push-Location $RepoRoot
try {
  Write-Step "validate compose files without reading .env"
  $env:COMPOSE_DISABLE_ENV_FILE = "1"
  Invoke-Checked { docker compose config | Out-Null }
  Invoke-Checked { docker compose -f docker-compose.ci.yml config --quiet | Out-Null }

  Write-Step "verify Phase2.5 docs and scripts"
  @(
    "docs/Phase2_Phase3_RAG拡張実装計画書_改訂版.md",
    "docs/phase2/phase2_5_readme.md",
    "docs/phase2/context_engineering_readme.md",
    "docs/phase2/context_engineering_demo_scenario.md",
    "docs/phase2/context_engineering_manual_test_cases.md",
    "docs/phase2/context_engineering_acceptance_checklist.md",
    "docs/phase2/context_engineering_known_limitations.md",
    "docs/phase2/kubernetes_baseline.md",
    "docs/phase2/kubernetes_local_baseline.md",
    "docs/phase2/phase2_5_demo_scenario.md",
    "docs/phase2/phase3_handoff.md",
    "docs/phase2/deploy_aws_handoff.md",
    "docs/phase2/llm_tool_calling_retrieval_orchestrator.md",
    "docs/phase2/context_budget_trace_debug.md",
    "docs/phase2/evidence_pack_context_compression.md",
    "docs/phase2/tool_result_compression_orchestrator_guard.md",
    "docs/phase2/mcp_advanced_rag_tools.md",
    "k8s/local/kustomization.yaml",
    "scripts/smoke_phase2_5.ps1",
    "scripts/k8s_smoke.ps1",
    "scripts/validate_k8s_manifests.py"
  ) | ForEach-Object { Assert-PathExists $_ }

  Write-Step "run local manifest validator"
  Invoke-Checked { python scripts\validate_k8s_manifests.py }

  $kubectl = Get-Command kubectl -ErrorAction SilentlyContinue
  if ($null -ne $kubectl) {
    Write-Step "render local k8s manifests"
    Invoke-Checked { kubectl kustomize k8s/local | Out-Null }
    if ($K8sDryRun) {
      Write-Step "run client-side local k8s dry-run"
      Invoke-Checked { kubectl apply --dry-run=client -k k8s/local | Out-Null }
    }
  } else {
    Write-Step "kubectl not found; skipping k8s render and dry-run"
  }

  Write-Step "check running backend/frontend if available"
  if (Test-Url "$BackendUrl/health") {
    if (-not (Test-Url "$BackendUrl/ready")) { throw "backend readiness failed" }
  } else {
    Write-Step "backend is not reachable; start services before -Deep"
  }
  if (-not (Test-Url $FrontendUrl)) {
    Write-Step "frontend is not reachable; UI smoke skipped"
  }

  if ($RunExistingPhase2) {
    Write-Step "run existing Phase2 smoke wrapper"
    & (Join-Path $RepoRoot "scripts/smoke_phase2.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }

  if (-not $Deep) {
    Write-Step "basic Phase2.5 smoke completed"
    exit 0
  }

  if (-not (Test-Url "$BackendUrl/ready")) {
    throw "Deep smoke requires a running ready backend at $BackendUrl"
  }
  if ([string]::IsNullOrWhiteSpace($AdminEmail) -or [string]::IsNullOrWhiteSpace($AdminPassword)) {
    throw "Deep smoke requires SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD in the shell environment."
  }

  Write-Step "deep smoke checks require local app auth; credentials are not printed"
  Write-Step "run Auto, Retrieval Debug, Context Budget, Evidence Pack, Tool Result Compression, and MCP checks manually from docs/phase2/phase2_5_demo_scenario.md"
  Write-Step "deep Phase2.5 smoke completed"
} finally {
  Pop-Location
}
