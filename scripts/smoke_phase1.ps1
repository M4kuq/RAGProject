param(
  [switch]$Deep
)

$ErrorActionPreference = "Stop"
$BackendUrl = if ($env:BACKEND_URL) { $env:BACKEND_URL } else { "http://localhost:8000" }
$FrontendUrl = if ($env:FRONTEND_URL) { $env:FRONTEND_URL } else { "http://localhost:5173" }

function Write-Step([string]$Message) {
  Write-Host "[phase1-smoke] $Message"
}

function Invoke-Compose([string[]]$ArgsList) {
  docker compose @ArgsList
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

Write-Step "validate compose files"
Invoke-Compose @("config")
Invoke-Compose @("-f", "docker-compose.ci.yml", "config")

Write-Step "check backend health when running"
if (Test-Url "$BackendUrl/health") {
  if (-not (Test-Url "$BackendUrl/ready")) { throw "backend readiness failed" }
} else {
  Write-Step "backend is not reachable; run docker compose up --build or use -Deep"
}

Write-Step "check frontend when running"
if (-not (Test-Url $FrontendUrl)) {
  Write-Step "frontend is not reachable; run docker compose up --build or use -Deep"
}

if (-not $Deep) {
  Write-Step "basic smoke completed"
  exit 0
}

Write-Step "build and start Phase1 services"
Invoke-Compose @("build", "backend", "worker", "frontend", "migrate", "seed")
Invoke-Compose @("run", "--rm", "migrate")
Invoke-Compose @("run", "--rm", "seed")
Invoke-Compose @("up", "-d", "backend", "worker", "frontend")

Write-Step "wait for backend readiness"
$ready = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
  if (Test-Url "$BackendUrl/ready") { $ready = $true; break }
  Start-Sleep -Seconds 2
}
if (-not $ready) { throw "backend readiness failed" }

Write-Step "check qdrant from backend network"
Invoke-Compose @("exec", "-T", "backend", "python", "-m", "app.scripts.healthcheck", "http://qdrant:6333/healthz")

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$uploadPath = Join-Path ([System.IO.Path]::GetTempPath()) "phase1-smoke-upload.md"
Set-Content -Path $uploadPath -Value "# Phase1 smoke upload`nThis local smoke document confirms upload, ingest queue creation, and admin approval paths." -Encoding UTF8
try {
  Write-Step "login with local demo admin"
  $csrf = Invoke-RestMethod -WebSession $session -Uri "$BackendUrl/api/v1/auth/csrf"
  $csrfToken = $csrf.data.csrf_token
  $loginBody = @{ email = "admin@example.com"; password = "password" } | ConvertTo-Json -Compress
  $login = Invoke-RestMethod -WebSession $session -Method Post -Uri "$BackendUrl/api/v1/auth/login" -ContentType "application/json" -Headers @{ "X-CSRF-Token" = $csrfToken } -Body $loginBody
  $csrfToken = $login.data.csrf_token

  Write-Step "list seeded documents"
  Invoke-RestMethod -WebSession $session -Uri "$BackendUrl/api/v1/documents?page_size=5" | Out-Null

  Write-Step "upload a small smoke document"
  $upload = Invoke-RestMethod -WebSession $session -Method Post -Uri "$BackendUrl/api/v1/documents" -Headers @{ "X-CSRF-Token" = $csrfToken } -Form @{ title = "Phase1 Smoke Upload"; file = Get-Item $uploadPath }
  $logicalDocumentId = $upload.data.logical_document_id
  $documentVersionId = $upload.data.document_version_id

  Write-Step "approve uploaded document version"
  Invoke-RestMethod -WebSession $session -Method Post -Uri "$BackendUrl/api/v1/documents/$logicalDocumentId/versions/$documentVersionId/approve" -Headers @{ "X-CSRF-Token" = $csrfToken } | Out-Null

  Write-Step "run RAG search"
  $searchBody = @{ query = "What vector database is used by Phase1?"; top_k = 5; rerank_top_n = 2 } | ConvertTo-Json -Compress
  Invoke-RestMethod -WebSession $session -Method Post -Uri "$BackendUrl/api/v1/rag/search" -ContentType "application/json" -Headers @{ "X-CSRF-Token" = $csrfToken } -Body $searchBody | Out-Null

  Write-Step "create evaluation run"
  $evalBody = @{ dataset_name = "phase1_smoke"; case_limit = 1 } | ConvertTo-Json -Compress
  Invoke-RestMethod -WebSession $session -Method Post -Uri "$BackendUrl/api/v1/evaluations/runs" -ContentType "application/json" -Headers @{ "X-CSRF-Token" = $csrfToken } -Body $evalBody | Out-Null
} finally {
  Remove-Item -LiteralPath $uploadPath -ErrorAction SilentlyContinue
}

Write-Step "check MCP server version and tool list"
Invoke-Compose @("exec", "-T", "backend", "python", "-m", "app.mcp.server", "--version")
'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | docker compose exec -T backend python -m app.mcp.server | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Step "deep smoke completed"
