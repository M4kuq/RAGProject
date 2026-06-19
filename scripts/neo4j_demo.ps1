param(
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$Manifest = "docs/demo/corpus_manifest.json",
  [string]$Dataset = "phase3_graph_multi_hop",
  [string]$Strategies = "graph_postgres,graph_neo4j",
  [int]$CaseLimit = 5,
  [int]$TimeoutSeconds = 300,
  [switch]$SkipCorpus,
  [switch]$SkipEvaluation,
  [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ComposeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.neo4j-demo.yml")

function Write-Step([string]$Message) {
  Write-Host "[neo4j-demo] $Message"
}

function Invoke-Checked([scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

function Wait-HttpReady([string]$Url, [int]$TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5 | Out-Null
      return
    } catch {
      Start-Sleep -Seconds 2
    }
  }
  throw "timed out waiting for $Url"
}

$env:NEO4J_USER = if ($env:NEO4J_USER) { $env:NEO4J_USER } else { "neo4j" }
$env:NEO4J_PASSWORD = if ($env:NEO4J_PASSWORD) { $env:NEO4J_PASSWORD } else { "change-me-local" }

Push-Location $RepoRoot
try {
  Write-Step "validate compose config"
  Invoke-Checked { docker compose config --quiet | Out-Null }
  Invoke-Checked { docker compose @ComposeFiles --profile neo4j config --quiet | Out-Null }

  Write-Step "start compose stack with neo4j profile"
  if ($NoBuild) {
    Invoke-Checked { docker compose @ComposeFiles --profile neo4j up -d }
  } else {
    Invoke-Checked { docker compose @ComposeFiles --profile neo4j up -d --build }
  }

  Write-Step "wait for backend readiness"
  Wait-HttpReady "$BackendUrl/ready" $TimeoutSeconds

  if (-not $SkipCorpus) {
    Write-Step "ingest reproducible demo corpus through the existing API"
    Invoke-Checked {
      docker compose @ComposeFiles exec -T backend python -m app.scripts.ingest_demo_corpus `
        --repo-root /workspace `
        --manifest $Manifest `
        --base-url http://127.0.0.1:8000
    }
  }

  Write-Step "build PostgreSQL graph index and run optional Neo4j projection"
  Invoke-Checked { docker compose @ComposeFiles exec -T backend python -m app.scripts.build_demo_graph_index }

  if (-not $SkipEvaluation) {
    Write-Step "compare graph_postgres and graph_neo4j with existing evaluation runner"
    Invoke-Checked {
      docker compose @ComposeFiles exec -T backend python -m app.scripts.retrieval_eval_smoke `
        --dataset $Dataset `
        --strategies $Strategies `
        --threshold-mode warn `
        --case-limit $CaseLimit `
        --timeout-seconds $TimeoutSeconds `
        --output-json /tmp/ragproject_graph_provider_eval.json `
        --output-md /tmp/ragproject_graph_provider_eval.md
    }
  }

  Write-Step "Neo4j demo profile is running with GRAPH_STORE_PROVIDER=neo4j"
} finally {
  Pop-Location
}
