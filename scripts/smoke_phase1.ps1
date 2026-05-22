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

function Invoke-CurlJson([string[]]$ArgsList) {
  $output = & curl.exe @ArgsList
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  return ($output | ConvertFrom-Json)
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

$cookiePath = Join-Path ([System.IO.Path]::GetTempPath()) ("phase1-smoke-{0}.cookies" -f ([guid]::NewGuid()))
$uploadPath = Join-Path ([System.IO.Path]::GetTempPath()) "phase1-smoke-upload.md"
Set-Content -Path $uploadPath -Value "# Phase1 smoke upload`nThis local smoke document confirms upload, ingest queue creation, and admin approval paths." -Encoding UTF8
try {
  Write-Step "login with local demo admin"
  $csrf = Invoke-CurlJson @("-fsS", "-c", $cookiePath, "$BackendUrl/api/v1/auth/csrf")
  $csrfToken = $csrf.data.csrf_token
  $login = Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath, "-c", $cookiePath,
    "-H", "Content-Type: application/json",
    "-H", "X-CSRF-Token: $csrfToken",
    "-d", '{"email":"admin@example.com","password":"password"}',
    "$BackendUrl/api/v1/auth/login"
  )
  $csrfToken = $login.data.csrf_token

  Write-Step "list seeded documents"
  Invoke-CurlJson @("-fsS", "-b", $cookiePath, "$BackendUrl/api/v1/documents?page_size=5") | Out-Null

  Write-Step "upload a small smoke document"
  $upload = Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath, "-c", $cookiePath,
    "-H", "X-CSRF-Token: $csrfToken",
    "-F", "title=Phase1 Smoke Upload",
    "-F", "file=@$uploadPath;type=text/markdown",
    "$BackendUrl/api/v1/documents"
  )
  $logicalDocumentId = $upload.data.logical_document_id
  $documentVersionId = $upload.data.document_version_id

  Write-Step "approve uploaded document version"
  Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath,
    "-H", "X-CSRF-Token: $csrfToken",
    "-X", "POST",
    "$BackendUrl/api/v1/documents/$logicalDocumentId/versions/$documentVersionId/approve"
  ) | Out-Null

  Write-Step "run RAG search"
  Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath,
    "-H", "Content-Type: application/json",
    "-H", "X-CSRF-Token: $csrfToken",
    "-d", '{"query":"What vector database is used by Phase1?","top_k":5,"rerank_top_n":2}',
    "$BackendUrl/api/v1/rag/search"
  ) | Out-Null

  Write-Step "create evaluation run"
  Invoke-CurlJson @(
    "-fsS", "-b", $cookiePath,
    "-H", "Content-Type: application/json",
    "-H", "X-CSRF-Token: $csrfToken",
    "-d", '{"dataset_name":"phase1_smoke","case_limit":1}',
    "$BackendUrl/api/v1/evaluations/runs"
  ) | Out-Null
} finally {
  Remove-Item -LiteralPath $uploadPath -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $cookiePath -ErrorAction SilentlyContinue
}

Write-Step "check MCP server version and tool list"
Invoke-Compose @("exec", "-T", "backend", "python", "-m", "app.mcp.server", "--version")
'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | docker compose exec -T backend python -m app.mcp.server | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Step "run MCP rag_ask"
'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"rag_ask","arguments":{"question":"How does Phase1 keep CI deterministic?","top_k":5,"rerank_top_n":2}}}' | docker compose exec -T backend python -m app.mcp.server | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Step "deep smoke completed"
