param(
  [switch]$Deep,
  [switch]$RunRetrievalEval,
  [switch]$RunExperimentDryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendUrl = if ($env:BACKEND_URL) { $env:BACKEND_URL } else { "http://localhost:8000" }
$FrontendUrl = if ($env:FRONTEND_URL) { $env:FRONTEND_URL } else { "http://localhost:5173" }
$AdminEmail = $env:SMOKE_ADMIN_EMAIL
$AdminPassword = $env:SMOKE_ADMIN_PASSWORD

function Write-Step([string]$Message) {
  Write-Host "[phase2-smoke] $Message"
}

function Invoke-Compose([string[]]$ArgsList) {
  docker compose @ArgsList
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-ComposeQuiet([string[]]$ArgsList) {
  docker compose @ArgsList | Out-Null
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Assert-PathExists([string]$RelativePath) {
  $path = Join-Path $RepoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $path)) {
    throw "required Phase2 artifact is missing: $RelativePath"
  }
}

function Test-Url([string]$Url) {
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Invoke-CurlJson([string[]]$ArgsList) {
  $output = & curl.exe @ArgsList
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  return ($output | ConvertFrom-Json)
}

Write-Step "validate compose files"
Invoke-ComposeQuiet @("config")
Invoke-ComposeQuiet @("-f", "docker-compose.ci.yml", "config", "--quiet")

Write-Step "verify Phase2 final docs and artifacts"
@(
  "docs/phase2/README.md",
  "docs/phase2/phase2_demo_scenario.md",
  "docs/phase2/phase2_manual_test_cases.md",
  "docs/phase2/phase2_acceptance_checklist.md",
  "docs/phase2/phase2_known_limitations.md",
  "docs/phase2/phase3_handoff.md",
  "docs/phase2/retrieval_debug_ui_v2.md",
  "docs/phase2/agentic_retrieval_loop.md",
  "docs/phase2/agentic_strategy_evaluation.md",
  "docs/phase2/ci_retrieval_evaluation.md",
  "docs/phase2/langsmith_optional_adapter.md",
  "docs/phase2/sentence_transformers_experiment_harness.md",
  "docs/phase2/advanced_import_office.md",
  "docs/phase2/advanced_import_html_xml_url.md",
  "docs/phase2/document_diff_version_compare.md",
  "docs/phase2/citation_navigation.md",
  "backend/app/evaluation/fixtures/phase2_strategy_smoke.json",
  "backend/app/experiments/manifests/phase2_retrieval_models.example.json",
  ".github/workflows/retrieval-eval-smoke.yml"
) | ForEach-Object { Assert-PathExists $_ }

Write-Step "check backend health when running"
if (Test-Url "$BackendUrl/health") {
  if (-not (Test-Url "$BackendUrl/ready")) { throw "backend readiness failed" }
} else {
  Write-Step "backend is not reachable; start services or rerun with -Deep after startup"
}

Write-Step "check frontend when running"
if (-not (Test-Url $FrontendUrl)) {
  Write-Step "frontend is not reachable; start services if UI smoke is needed"
}

if ($RunExperimentDryRun) {
  Write-Step "run SentenceTransformers experiment dry-run without model download"
  & (Join-Path $RepoRoot "scripts/run_retrieval_model_experiment.ps1") `
    -Mode dry-run `
    -DownloadPolicy never `
    -SkipSeedIndexing
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($RunRetrievalEval) {
  Write-Step "run retrieval evaluation smoke wrapper"
  & (Join-Path $RepoRoot "scripts/run_retrieval_eval_smoke.ps1") `
    -Dataset phase2_strategy_smoke `
    -Strategies dense,hybrid,agentic_router `
    -ThresholdMode warn `
    -CaseLimit 5
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $Deep) {
  Write-Step "basic Phase2 smoke completed"
  exit 0
}

if (-not (Test-Url "$BackendUrl/ready")) {
  throw "Deep smoke requires a running ready backend at $BackendUrl"
}
if ([string]::IsNullOrWhiteSpace($AdminEmail) -or [string]::IsNullOrWhiteSpace($AdminPassword)) {
  throw "Deep smoke requires SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD to be set for the local demo admin."
}

$tempPrefix = [guid]::NewGuid()
$cookiePath = Join-Path ([System.IO.Path]::GetTempPath()) ("phase2-smoke-{0}.cookies" -f $tempPrefix)
$loginBodyPath = Join-Path ([System.IO.Path]::GetTempPath()) ("phase2-smoke-{0}.login.json" -f $tempPrefix)
try {
  Write-Step "sign in with local demo admin"
  $csrf = Invoke-CurlJson @("-fsS", "-c", $cookiePath, "$BackendUrl/api/v1/auth/csrf")
  $csrfToken = $csrf.data.csrf_token
  $loginBody = @{ email = $AdminEmail; password = $AdminPassword } | ConvertTo-Json -Compress
  [System.IO.File]::WriteAllText(
    $loginBodyPath,
    $loginBody,
    (New-Object System.Text.UTF8Encoding($false))
  )
  $login = Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath, "-c", $cookiePath,
    "-H", "Content-Type: application/json",
    "-H", "X-CSRF-Token: $csrfToken",
    "-d", "@$loginBodyPath",
    "$BackendUrl/api/v1/auth/login"
  )
  $csrfToken = $login.data.csrf_token

  Write-Step "run dense/sparse/hybrid/agentic_router safe searches"
  foreach ($strategy in @("dense", "sparse", "hybrid", "agentic_router")) {
    $body = @{
      query = "Phase2 retrieval strategy overview"
      top_k = 5
      rerank_top_n = 2
      strategy = $strategy
    } | ConvertTo-Json -Compress
    Invoke-CurlJson @(
      "-fsS", "-b", $cookiePath,
      "-H", "Content-Type: application/json",
      "-H", "X-CSRF-Token: $csrfToken",
      "-d", $body,
      "$BackendUrl/api/v1/rag/search"
    ) | Out-Null
  }

  Write-Step "verify retrieval debug endpoint is reachable for admin"
  $body = @{
    query = "Phase2 debug trace fields"
    top_k = 3
    rerank_top_n = 1
    strategy = "agentic_router"
  } | ConvertTo-Json -Compress
  $search = Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath,
    "-H", "Content-Type: application/json",
    "-H", "X-CSRF-Token: $csrfToken",
    "-d", $body,
    "$BackendUrl/api/v1/rag/search"
  )
  $runId = $search.data.retrieval_run_id
  if ($null -ne $runId) {
    Invoke-CurlJson @("-fsS", "-b", $cookiePath, "$BackendUrl/api/v1/rag/retrieval-runs/$runId") | Out-Null
  }
} finally {
  Remove-Item -LiteralPath $cookiePath -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $loginBodyPath -ErrorAction SilentlyContinue
}

Write-Step "deep Phase2 smoke completed"
