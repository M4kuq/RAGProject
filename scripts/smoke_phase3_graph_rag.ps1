param(
  [switch]$Deep
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendUrl = if ($env:BACKEND_URL) { $env:BACKEND_URL } else { "http://localhost:8000" }
$FrontendUrl = if ($env:FRONTEND_URL) { $env:FRONTEND_URL } else { "http://localhost:5173" }

function Write-Step([string]$Message) {
  Write-Host "[phase3-graph-smoke] $Message"
}

function Invoke-Checked([scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Assert-PathExists([string]$RelativePath) {
  $path = Join-Path $RepoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $path)) {
    throw "required GraphRAG artifact is missing: $RelativePath"
  }
}

function Assert-FileContains([string]$RelativePath, [string]$Pattern) {
  $path = Join-Path $RepoRoot $RelativePath
  if (-not (Select-String -LiteralPath $path -Pattern $Pattern -SimpleMatch -Quiet)) {
    throw "required GraphRAG text is missing from ${RelativePath}: $Pattern"
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

Push-Location $RepoRoot
try {
  Write-Step "validate compose files without reading .env"
  $env:COMPOSE_DISABLE_ENV_FILE = "1"
  Invoke-Checked { docker compose config --quiet | Out-Null }
  Invoke-Checked { docker compose --profile neo4j config --quiet | Out-Null }

  Write-Step "verify GraphRAG final docs and helper artifacts"
  @(
    "README.md",
    "docs/phase3/README.md",
    "docs/phase3/graph_rag_final_readme.md",
    "docs/phase3/graph_rag_demo_scenario.md",
    "docs/phase3/graph_rag_manual_test_cases.md",
    "docs/phase3/graph_rag_acceptance_checklist.md",
    "docs/phase3/graph_rag_known_limitations.md",
    "docs/phase3/graph_rag_next_phase_handoff.md",
    "docs/phase3/graph_rag_architecture.md",
    "docs/phase3/graph_retrieval_strategy.md",
    "docs/phase3/neo4j_optional_backend.md",
    "docs/phase3/retrieval_cache_foundation.md",
    "docs/phase3/graph_evaluation_design.md",
    "docs/phase3/security_redaction_policy.md",
    "backend/app/evaluation/fixtures/phase3_graph_multi_hop.json",
    "backend/app/scripts/queue_graph_index_builds.py",
    "scripts/smoke_phase3_graph_rag.ps1",
    "scripts/smoke_phase3_graph_rag.sh"
  ) | ForEach-Object { Assert-PathExists $_ }

  Write-Step "verify key GraphRAG handoff statements"
  Assert-FileContains "docs/phase3/graph_rag_final_readme.md" "PostgreSQL is the source of truth"
  Assert-FileContains "docs/phase3/graph_rag_final_readme.md" "Neo4j, when enabled, is only a read model"
  Assert-FileContains "docs/phase3/graph_rag_final_readme.md" "RETRIEVAL_CACHE_ENABLED=false"
  Assert-FileContains "docs/phase3/graph_rag_final_readme.md" "phase3_graph_multi_hop"
  Assert-FileContains "docs/phase3/graph_rag_acceptance_checklist.md" "Raw text and secret non-storage"
  Assert-FileContains "docs/phase3/graph_rag_known_limitations.md" "graph_hybrid"
  Assert-FileContains "docker-compose.yml" "GRAPH_RETRIEVAL_ENABLED"
  Assert-FileContains ".env.example" "GRAPH_RETRIEVAL_ENABLED=false"

  Write-Step "check running backend/frontend if available"
  if (Test-Url "$BackendUrl/health") {
    if (-not (Test-Url "$BackendUrl/ready")) { throw "backend readiness failed" }
  } else {
    Write-Step "backend is not reachable; start services for runtime demo checks"
  }
  if (-not (Test-Url $FrontendUrl)) {
    Write-Step "frontend is not reachable; UI demo checks skipped"
  }

  if ($Deep) {
    Write-Step "deep mode validates helper import without queueing jobs"
    Invoke-Checked {
      docker compose exec -T backend python -m app.scripts.queue_graph_index_builds --dry-run | Out-Null
    }
  }

  Write-Step "GraphRAG smoke completed"
} finally {
  Pop-Location
}
