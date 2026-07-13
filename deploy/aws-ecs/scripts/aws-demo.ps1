[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("doctor", "plan", "up", "load-data", "smoke", "status", "down")]
  [string]$Command,
  [switch]$ConfirmDestroy,
  [string]$DestroyConfirmation = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ExpectedBranch = "deploy/AWS_ECS"
$script:ExpectedRegion = "ap-northeast-1"
$script:DestroyPhrase = "DESTROY-RUNTIME"
$script:TerraformDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$script:RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../../.."))
$script:ArtifactDirectory = Join-Path $script:TerraformDirectory ".aws-demo"
$script:PlanPath = Join-Path $script:ArtifactDirectory "runtime.tfplan"
$script:PlanManifestPath = Join-Path $script:ArtifactDirectory "runtime-plan.json"
$script:ScalePlanPath = Join-Path $script:ArtifactDirectory "scale-up.tfplan"
$script:ScaleManifestPath = Join-Path $script:ArtifactDirectory "scale-up-plan.json"
$script:DestroyPlanPath = Join-Path $script:ArtifactDirectory "destroy.tfplan"
$script:DestroyManifestPath = Join-Path $script:ArtifactDirectory "destroy-plan.json"

function Write-Step {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-Host "==> $Message"
}

function Invoke-Native {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$ArgumentList,
    [switch]$Capture
  )
  if ($Capture) {
    $output = & $FilePath @ArgumentList 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "$FilePath failed with exit code $exitCode." }
    return (($output | Out-String).Trim())
  }
  & $FilePath @ArgumentList
  if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE." }
}

function Assert-CommandAvailable {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command is unavailable: $Name"
  }
}

function Assert-RequiredEnvironment {
  param([Parameter(Mandatory = $true)][string[]]$Names)
  $missing = @(
    foreach ($name in $Names) {
      if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { $name }
    }
  )
  if ($missing.Count -gt 0) {
    throw "Required environment variables are missing: $($missing -join ', ')"
  }
}

function Test-DemoAccountAllowed {
  param(
    [Parameter(Mandatory = $true)][string]$AccountId,
    [Parameter(Mandatory = $true)][string]$Allowlist
  )
  if ($AccountId -notmatch "^[0-9]{12}$") { return $false }
  $allowed = @(
    $Allowlist.Split(",") |
      ForEach-Object { $_.Trim() } |
      Where-Object { $_ -match "^[0-9]{12}$" }
  )
  return $allowed -contains $AccountId
}

function Get-DemoIdentity {
  Assert-RequiredEnvironment @("AWS_DEMO_ALLOWED_ACCOUNT_IDS")
  $json = Invoke-Native "aws" @(
    "sts", "get-caller-identity",
    "--region", $script:ExpectedRegion,
    "--output", "json",
    "--no-cli-pager"
  ) -Capture
  $identity = $json | ConvertFrom-Json
  $accountId = [string]$identity.Account
  if (-not (Test-DemoAccountAllowed $accountId $env:AWS_DEMO_ALLOWED_ACCOUNT_IDS)) {
    throw "The active AWS account is not in AWS_DEMO_ALLOWED_ACCOUNT_IDS."
  }
  return [pscustomobject]@{ AccountId = $accountId; Arn = [string]$identity.Arn }
}

function Assert-DemoContext {
  foreach ($tool in @("git", "terraform", "aws")) { Assert-CommandAvailable $tool }
  $branch = Invoke-Native "git" @("-C", $script:RepoRoot, "branch", "--show-current") -Capture
  if ($branch -ne $script:ExpectedBranch) {
    throw "This command is restricted to branch $script:ExpectedBranch."
  }
  $status = Invoke-Native "git" @(
    "-C", $script:RepoRoot, "status", "--porcelain", "--untracked-files=all"
  ) -Capture
  if (-not [string]::IsNullOrWhiteSpace($status)) {
    throw "The AWS demo worktree must be clean."
  }
  $region = if ([string]::IsNullOrWhiteSpace($env:AWS_REGION)) {
    $env:AWS_DEFAULT_REGION
  } else {
    $env:AWS_REGION
  }
  if ($region -ne $script:ExpectedRegion) {
    throw "AWS_REGION must be $script:ExpectedRegion."
  }
  return [pscustomobject]@{
    Identity = Get-DemoIdentity
    GitSha = Invoke-Native "git" @("-C", $script:RepoRoot, "rev-parse", "HEAD") -Capture
  }
}

function Assert-BackendEnvironment {
  Assert-RequiredEnvironment @(
    "TF_STATE_BUCKET",
    "TF_STATE_KEY",
    "TF_LOCK_TABLE",
    "TF_VAR_github_oidc_repo",
    "TF_VAR_github_deploy_branch",
    "TF_VAR_create_github_oidc_provider",
    "TF_VAR_github_oidc_provider_arn",
    "TF_VAR_database_url_secret_arn",
    "TF_VAR_session_secret_arn",
    "TF_VAR_alb_origin_domain_name",
    "TF_VAR_alb_certificate_arn",
    "TF_VAR_route53_hosted_zone_id",
    "TF_VAR_basic_auth_username",
    "TF_VAR_basic_auth_header_sha256",
    "TF_VAR_origin_verify_header_value"
  )
  if ($env:TF_VAR_github_deploy_branch -ne $script:ExpectedBranch) {
    throw "TF_VAR_github_deploy_branch must be $script:ExpectedBranch."
  }
  if ($env:TF_VAR_region -ne $script:ExpectedRegion) {
    throw "TF_VAR_region must be $script:ExpectedRegion."
  }
  if ($env:TF_VAR_create_github_oidc_provider -ne "false") {
    throw "The lifecycle requires an external bootstrap OIDC provider."
  }
}

function Initialize-DemoTerraform {
  New-Item -ItemType Directory -Path $script:ArtifactDirectory -Force | Out-Null
  Push-Location $script:TerraformDirectory
  try {
    Invoke-Native "terraform" @(
      "init", "-input=false", "-reconfigure",
      "-backend-config=bucket=$env:TF_STATE_BUCKET",
      "-backend-config=key=$env:TF_STATE_KEY",
      "-backend-config=region=$script:ExpectedRegion",
      "-backend-config=dynamodb_table=$env:TF_LOCK_TABLE",
      "-backend-config=encrypt=true"
    )
    Invoke-Native "terraform" @("fmt", "-check", "-recursive")
    Invoke-Native "terraform" @("validate")
  } finally {
    Pop-Location
  }
}

function Assert-PlanPreservesBootstrapIdentity {
  param([Parameter(Mandatory = $true)][string]$PlanPath)
  Push-Location $script:TerraformDirectory
  try {
    $plan = (Invoke-Native "terraform" @("show", "-json", $PlanPath) -Capture) | ConvertFrom-Json
  } finally {
    Pop-Location
  }
  $providerChanges = @(
    $plan.resource_changes |
      Where-Object {
        $_.address -match "aws_iam_openid_connect_provider" -and
        (@($_.change.actions) -join ",") -ne "no-op"
      }
  )
  if ($providerChanges.Count -gt 0) {
    Remove-Item -LiteralPath $PlanPath -Force -ErrorAction SilentlyContinue
    throw "Runtime plans must not create or destroy the bootstrap GitHub OIDC provider."
  }
}

function Write-PlanManifest {
  param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$Kind,
    [Parameter(Mandatory = $true)]$Context
  )
  [ordered]@{
    schema_version = 1
    kind = $Kind
    plan_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $PlanPath).Hash.ToLowerInvariant()
    git_sha = $Context.GitSha
    account_id = $Context.Identity.AccountId
    region = $script:ExpectedRegion
    branch = $script:ExpectedBranch
    created_at_utc = [DateTimeOffset]::UtcNow.ToString("O")
  } | ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}

function Assert-SavedPlan {
  param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$ExpectedKind,
    [Parameter(Mandatory = $true)]$Context
  )
  if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf) -or
      -not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Saved plan or manifest is missing."
  }
  $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
  $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PlanPath).Hash.ToLowerInvariant()
  if ($manifest.schema_version -ne 1 -or
      $manifest.kind -ne $ExpectedKind -or
      $manifest.plan_sha256 -ne $actualHash -or
      $manifest.git_sha -ne $Context.GitSha -or
      $manifest.account_id -ne $Context.Identity.AccountId -or
      $manifest.region -ne $script:ExpectedRegion -or
      $manifest.branch -ne $script:ExpectedBranch) {
    throw "Saved plan manifest does not match the current execution context."
  }
}

function New-DemoPlan {
  param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$Kind,
    [Parameter(Mandatory = $true)]$Context,
    [Parameter(Mandatory = $true)][int]$ApiCount,
    [Parameter(Mandatory = $true)][int]$WorkerCount,
    [Parameter(Mandatory = $true)][int]$QdrantCount,
    [string]$ApiImageTag = "placeholder",
    [string]$WorkerImageTag = "placeholder"
  )
  Push-Location $script:TerraformDirectory
  try {
    Invoke-Native "terraform" @(
      "plan", "-input=false", "-lock-timeout=5m",
      "-out=$PlanPath",
      "-var=api_desired_count=$ApiCount",
      "-var=worker_desired_count=$WorkerCount",
      "-var=qdrant_desired_count=$QdrantCount",
      "-var=api_image_tag=$ApiImageTag",
      "-var=worker_image_tag=$WorkerImageTag"
    )
  } finally {
    Pop-Location
  }
  Assert-PlanPreservesBootstrapIdentity $PlanPath
  Write-PlanManifest $PlanPath $ManifestPath $Kind $Context
}

function Apply-SavedPlan {
  param([Parameter(Mandatory = $true)][string]$PlanPath)
  Push-Location $script:TerraformDirectory
  try {
    Invoke-Native "terraform" @("apply", "-input=false", "-lock-timeout=5m", $PlanPath)
  } finally {
    Pop-Location
  }
}

function Get-TerraformOutput {
  param([Parameter(Mandatory = $true)][string]$Name)
  Push-Location $script:TerraformDirectory
  try {
    return Invoke-Native "terraform" @("output", "-raw", $Name) -Capture
  } finally {
    Pop-Location
  }
}

function Get-TerraformJsonOutput {
  param([Parameter(Mandatory = $true)][string]$Name)
  Push-Location $script:TerraformDirectory
  try {
    return (Invoke-Native "terraform" @("output", "-json", $Name) -Capture) | ConvertFrom-Json
  } finally {
    Pop-Location
  }
}

function Get-AppDeploymentConfig {
  Push-Location $script:TerraformDirectory
  try {
    $publicSubnets = (Invoke-Native "terraform" @("output", "-json", "public_subnet_ids") -Capture) | ConvertFrom-Json
  } finally {
    Pop-Location
  }
  return ([ordered]@{
    api_ecr_repository_url = Get-TerraformOutput "api_ecr_repository_url"
    worker_ecr_repository_url = Get-TerraformOutput "worker_ecr_repository_url"
    ecs_cluster_name = Get-TerraformOutput "ecs_cluster_name"
    ecs_public_subnet_ids = @($publicSubnets)
    ecs_app_security_group_id = Get-TerraformOutput "app_security_group_id"
    ecs_api_service_name = Get-TerraformOutput "api_service_name"
    ecs_worker_service_name = Get-TerraformOutput "worker_service_name"
    ecs_api_task_definition = Get-TerraformOutput "api_task_definition_family"
    ecs_worker_task_definition = Get-TerraformOutput "worker_task_definition_family"
    ecs_migration_task_definition = Get-TerraformOutput "migration_task_definition_family"
  } | ConvertTo-Json -Depth 4 -Compress)
}

function Get-FrontendDeploymentConfig {
  return ([ordered]@{
    frontend_bucket_name = Get-TerraformOutput "frontend_bucket_name"
    cloudfront_distribution_id = Get-TerraformOutput "cloudfront_distribution_id"
  } | ConvertTo-Json -Compress)
}

function Update-DatabaseUrlSecret {
  $masterArn = Get-TerraformOutput "rds_master_user_secret_arn"
  $secretJson = Invoke-Native "aws" @(
    "secretsmanager", "get-secret-value",
    "--secret-id", $masterArn,
    "--query", "SecretString",
    "--output", "text",
    "--region", $script:ExpectedRegion,
    "--no-cli-pager"
  ) -Capture
  $secret = $secretJson | ConvertFrom-Json
  $endpoint = Get-TerraformOutput "rds_endpoint"
  $databaseName = [Uri]::EscapeDataString((Get-TerraformOutput "database_name"))
  $username = [Uri]::EscapeDataString([string]$secret.username)
  $password = [Uri]::EscapeDataString([string]$secret.password)
  $databaseUrl = "postgresql+psycopg://$($username):$($password)@$endpoint/$databaseName"
  $secretFile = Join-Path $script:ArtifactDirectory "database-url.secret"
  try {
    Set-Content -LiteralPath $secretFile -Value $databaseUrl -Encoding UTF8 -NoNewline
    Invoke-Native "aws" @(
      "secretsmanager", "put-secret-value",
      "--secret-id", $env:TF_VAR_database_url_secret_arn,
      "--secret-string", "file://$secretFile",
      "--region", $script:ExpectedRegion,
      "--no-cli-pager"
    ) -Capture | Out-Null
  } finally {
    Remove-Item -LiteralPath $secretFile -Force -ErrorAction SilentlyContinue
    $databaseUrl = $null
    $secretJson = $null
    $secret = $null
  }
}

function Invoke-GitHubWorkflow {
  param(
    [Parameter(Mandatory = $true)][string]$Workflow,
    [hashtable]$Fields = @{}
  )
  Assert-CommandAvailable "gh"
  $startedAt = [DateTimeOffset]::UtcNow.AddSeconds(-5)
  $arguments = @("workflow", "run", $Workflow, "--ref", $script:ExpectedBranch)
  foreach ($entry in $Fields.GetEnumerator()) {
    $arguments += @("--field", "$($entry.Key)=$($entry.Value)")
  }
  Invoke-Native "gh" $arguments
  $runId = $null
  for ($attempt = 1; $attempt -le 24; $attempt++) {
    Start-Sleep -Seconds 5
    $json = Invoke-Native "gh" @(
      "run", "list",
      "--workflow", $Workflow,
      "--branch", $script:ExpectedBranch,
      "--event", "workflow_dispatch",
      "--limit", "10",
      "--json", "databaseId,createdAt"
    ) -Capture
    $candidate = @($json | ConvertFrom-Json) |
      Where-Object { [DateTimeOffset]::Parse($_.createdAt) -ge $startedAt } |
      Sort-Object { [DateTimeOffset]::Parse($_.createdAt) } -Descending |
      Select-Object -First 1
    if ($null -ne $candidate) {
      $runId = [string]$candidate.databaseId
      break
    }
  }
  if ([string]::IsNullOrWhiteSpace($runId)) {
    throw "Unable to locate the dispatched $Workflow run."
  }
  Invoke-Native "gh" @("run", "watch", $runId, "--exit-status")
}

function Invoke-Doctor {
  $context = Assert-DemoContext
  Write-Host "AWS demo context is valid."
  Write-Host "Branch: $script:ExpectedBranch"
  Write-Host "Region: $script:ExpectedRegion"
  Write-Host "Account: $($context.Identity.AccountId)"
}

function Invoke-Plan {
  $context = Assert-DemoContext
  Assert-BackendEnvironment
  Initialize-DemoTerraform
  New-DemoPlan $script:PlanPath $script:PlanManifestPath "runtime-create" $context 0 0 0
  Write-Host "Saved runtime plan and manifest under deploy/aws-ecs/.aws-demo."
}

function Invoke-Up {
  $context = Assert-DemoContext
  Assert-BackendEnvironment
  Assert-SavedPlan $script:PlanPath $script:PlanManifestPath "runtime-create" $context
  Write-Step "apply reviewed runtime plan"
  Apply-SavedPlan $script:PlanPath
  Write-Step "refresh app DATABASE_URL without printing credentials"
  Update-DatabaseUrlSecret
  Write-Step "build, migrate, and deploy application images"
  $appConfig = Get-AppDeploymentConfig
  Invoke-GitHubWorkflow "aws-deploy-app.yml" @{
    image_tag = $context.GitSha
    source_sha = $context.GitSha
    deployment_config = $appConfig
  }
  Write-Step "build and deploy frontend"
  $frontendConfig = Get-FrontendDeploymentConfig
  Invoke-GitHubWorkflow "aws-deploy-frontend.yml" @{
    source_sha = $context.GitSha
    deployment_config = $frontendConfig
  }
  Write-Step "create and apply exact scale-up plan"
  New-DemoPlan $script:ScalePlanPath $script:ScaleManifestPath "runtime-scale-up" $context 1 1 1 $context.GitSha $context.GitSha
  Assert-SavedPlan $script:ScalePlanPath $script:ScaleManifestPath "runtime-scale-up" $context
  Apply-SavedPlan $script:ScalePlanPath
  Write-Host "AWS demo runtime is up."
}

function Get-DemoBaseUrl {
  if (-not [string]::IsNullOrWhiteSpace($env:RAG_DEMO_BASE_URL)) {
    return $env:RAG_DEMO_BASE_URL.TrimEnd("/")
  }
  return "https://$(Get-TerraformOutput 'cloudfront_domain_name')"
}

function Invoke-LoadData {
  Assert-DemoContext | Out-Null
  Assert-BackendEnvironment
  Initialize-DemoTerraform
  Assert-CommandAvailable "uv"
  Assert-RequiredEnvironment @(
    "RAG_DEMO_ADMIN_EMAIL",
    "RAG_DEMO_ADMIN_PASSWORD",
    "RAG_DEMO_BASIC_AUTH_HEADER"
  )
  $baseUrl = Get-DemoBaseUrl
  $oldBaseUrl = $env:RAG_DEMO_BASE_URL
  $oldOrigin = $env:RAG_DEMO_ORIGIN
  try {
    $env:RAG_DEMO_BASE_URL = $baseUrl
    $env:RAG_DEMO_ORIGIN = $baseUrl
    Push-Location (Join-Path $script:RepoRoot "backend")
    try {
      Invoke-Native "uv" @(
        "run", "python", "-m", "app.scripts.ingest_demo_corpus",
        "--repo-root", $script:RepoRoot
      )
    } finally {
      Pop-Location
    }
  } finally {
    $env:RAG_DEMO_BASE_URL = $oldBaseUrl
    $env:RAG_DEMO_ORIGIN = $oldOrigin
  }
}

function Invoke-Smoke {
  Assert-DemoContext | Out-Null
  Assert-BackendEnvironment
  Initialize-DemoTerraform
  Assert-RequiredEnvironment @(
    "RAG_DEMO_ADMIN_EMAIL",
    "RAG_DEMO_ADMIN_PASSWORD",
    "RAG_DEMO_BASIC_AUTH_HEADER"
  )
  $baseUrl = Get-DemoBaseUrl
  $baseHeaders = @{ Authorization = $env:RAG_DEMO_BASIC_AUTH_HEADER; Origin = $baseUrl }
  $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $ready = Invoke-RestMethod -Method Get -Uri "$baseUrl/ready" -Headers $baseHeaders -WebSession $session
  $csrf = Invoke-RestMethod -Method Get -Uri "$baseUrl/api/v1/auth/csrf" -Headers $baseHeaders -WebSession $session
  $loginHeaders = @{
    Authorization = $env:RAG_DEMO_BASIC_AUTH_HEADER
    Origin = $baseUrl
    "X-CSRF-Token" = [string]$csrf.data.csrf_token
  }
  $loginBody = @{
    email = $env:RAG_DEMO_ADMIN_EMAIL
    password = $env:RAG_DEMO_ADMIN_PASSWORD
  } | ConvertTo-Json -Compress
  $login = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/auth/login" -Headers $loginHeaders -WebSession $session -ContentType "application/json" -Body $loginBody
  $query = if ([string]::IsNullOrWhiteSpace($env:RAG_DEMO_SMOKE_QUERY)) {
    "What vector database does this project use?"
  } else {
    $env:RAG_DEMO_SMOKE_QUERY
  }
  $searchHeaders = @{
    Authorization = $env:RAG_DEMO_BASIC_AUTH_HEADER
    Origin = $baseUrl
    "X-CSRF-Token" = [string]$login.data.csrf_token
  }
  $searchBody = @{
    query = $query
    top_k = 5
    rerank_top_n = 3
    cache_bypass = $true
  } | ConvertTo-Json -Compress
  $search = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/rag/search" -Headers $searchHeaders -WebSession $session -ContentType "application/json" -Body $searchBody
  $resultDir = Join-Path $script:ArtifactDirectory "results"
  New-Item -ItemType Directory -Path $resultDir -Force | Out-Null
  $resultCount = if ($null -ne $search.data.results) { @($search.data.results).Count } else { 0 }
  if ($resultCount -le 0) {
    throw "Smoke search returned no results."
  }
  [ordered]@{
    schema_version = 1
    checked_at_utc = [DateTimeOffset]::UtcNow.ToString("O")
    base_url = $baseUrl
    ready = ($null -ne $ready)
    authenticated = $true
    rag_search_succeeded = $true
    result_count = $resultCount
  } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $resultDir "smoke.json") -Encoding UTF8
  Write-Host "Smoke passed. A redacted result was written under deploy/aws-ecs/.aws-demo/results."
}

function Invoke-Status {
  $context = Assert-DemoContext
  Assert-BackendEnvironment
  Initialize-DemoTerraform
  $cluster = Get-TerraformOutput "ecs_cluster_name"
  $services = @(
    Get-TerraformOutput "api_service_name"
    Get-TerraformOutput "worker_service_name"
    Get-TerraformOutput "qdrant_service_name"
  )
  $arguments = @("ecs", "describe-services", "--cluster", $cluster, "--services") +
    $services +
    @("--region", $script:ExpectedRegion, "--output", "json", "--no-cli-pager")
  $status = (Invoke-Native "aws" $arguments -Capture) | ConvertFrom-Json
  Write-Host "Account: $($context.Identity.AccountId)"
  Write-Host "Endpoint: https://$(Get-TerraformOutput 'cloudfront_domain_name')"
  foreach ($service in @($status.services)) {
    Write-Host "$($service.serviceName): desired=$($service.desiredCount) running=$($service.runningCount) pending=$($service.pendingCount)"
  }
}

function Assert-DestroyRequested {
  param(
    [Parameter(Mandatory = $true)][bool]$Confirmed,
    [Parameter(Mandatory = $true)][string]$Phrase
  )
  if (-not $Confirmed -or $Phrase -cne $script:DestroyPhrase) {
    throw "Destroy requires -ConfirmDestroy and -DestroyConfirmation $script:DestroyPhrase."
  }
}

function Remove-AllBucketVersions {
  param([Parameter(Mandatory = $true)][string]$Bucket)
  for ($pass = 1; $pass -le 50; $pass++) {
    $json = Invoke-Native "aws" @(
      "s3api", "list-object-versions",
      "--bucket", $Bucket,
      "--region", $script:ExpectedRegion,
      "--output", "json",
      "--no-cli-pager"
    ) -Capture
    $listing = $json | ConvertFrom-Json
    $objects = @()
    $listedVersions = if ($null -ne $listing.PSObject.Properties["Versions"]) { @($listing.Versions) } else { @() }
    $deleteMarkers = if ($null -ne $listing.PSObject.Properties["DeleteMarkers"]) { @($listing.DeleteMarkers) } else { @() }
    foreach ($item in $listedVersions + $deleteMarkers) {
      if ($null -ne $item -and $null -ne $item.Key -and $null -ne $item.VersionId) {
        $objects += [ordered]@{ Key = [string]$item.Key; VersionId = [string]$item.VersionId }
      }
    }
    if ($objects.Count -eq 0) { return }
    for ($offset = 0; $offset -lt $objects.Count; $offset += 1000) {
      $end = [Math]::Min($offset + 999, $objects.Count - 1)
      $batch = @($objects[$offset..$end])
      $deleteFile = Join-Path $script:ArtifactDirectory "s3-delete-batch.json"
      try {
        @{ Objects = $batch; Quiet = $true } |
          ConvertTo-Json -Depth 5 -Compress |
          Set-Content -LiteralPath $deleteFile -Encoding UTF8
        Invoke-Native "aws" @(
          "s3api", "delete-objects",
          "--bucket", $Bucket,
          "--delete", "file://$deleteFile",
          "--region", $script:ExpectedRegion,
          "--output", "json",
          "--no-cli-pager"
        ) -Capture | Out-Null
      } finally {
        Remove-Item -LiteralPath $deleteFile -Force -ErrorAction SilentlyContinue
      }
    }
  }
  throw "Bucket version cleanup did not converge."
}

function Test-AwsResourceAbsent {
  param(
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [Parameter(Mandatory = $true)][string]$NotFoundPattern
  )
  $output = & aws @Arguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -eq 0) { return $false }
  if (($output | Out-String) -match $NotFoundPattern) { return $true }
  throw "AWS absence verification failed unexpectedly."
}

function Remove-ActiveTaskDefinitions {
  param([Parameter(Mandatory = $true)][string[]]$Families)

  foreach ($family in $Families) {
    $remaining = @()
    for ($attempt = 1; $attempt -le 7; $attempt++) {
      $listJson = Invoke-Native "aws" @(
        "ecs", "list-task-definitions",
        "--family-prefix", $family,
        "--status", "ACTIVE",
        "--sort", "DESC",
        "--region", $script:ExpectedRegion,
        "--output", "json",
        "--no-cli-pager"
      ) -Capture
      $exactPattern = "/" + [Regex]::Escape($family) + ":[0-9]+$"
      $remaining = @(
        ($listJson | ConvertFrom-Json).taskDefinitionArns |
          Where-Object { [string]$_ -match $exactPattern }
      )
      if ($remaining.Count -eq 0) { break }
      if ($attempt -eq 7) { break }

      foreach ($taskDefinitionArn in $remaining) {
        Invoke-Native "aws" @(
          "ecs", "deregister-task-definition",
          "--task-definition", [string]$taskDefinitionArn,
          "--region", $script:ExpectedRegion,
          "--output", "json",
          "--no-cli-pager"
        ) -Capture | Out-Null
      }
      Start-Sleep -Seconds 2
    }
    if ($remaining.Count -gt 0) {
      throw "Active task definition revisions remain for a runtime family."
    }
  }
}

function Clear-DatabaseUrlSecret {
  if ([string]::IsNullOrWhiteSpace($env:TF_VAR_database_url_secret_arn)) { return }
  $file = Join-Path $script:ArtifactDirectory "database-url.destroyed"
  try {
    Set-Content -LiteralPath $file -Value "destroyed://runtime-not-present" -Encoding UTF8 -NoNewline
    Invoke-Native "aws" @(
      "secretsmanager", "put-secret-value",
      "--secret-id", $env:TF_VAR_database_url_secret_arn,
      "--secret-string", "file://$file",
      "--region", $script:ExpectedRegion,
      "--no-cli-pager"
    ) -Capture | Out-Null
  } finally {
    Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
  }
}

function Assert-NoRuntimeRemnants {
  param([Parameter(Mandatory = $true)]$Snapshot)
  Push-Location $script:TerraformDirectory
  try { $state = Invoke-Native "terraform" @("state", "list") -Capture }
  finally { Pop-Location }
  if (-not [string]::IsNullOrWhiteSpace($state)) {
    throw "Terraform runtime state is not empty after destroy."
  }

  foreach ($bucket in @($Snapshot.Buckets)) {
    if (-not (Test-AwsResourceAbsent @(
      "s3api", "head-bucket", "--bucket", $bucket,
      "--region", $script:ExpectedRegion, "--no-cli-pager"
    ) "(404|NoSuchBucket|Not Found)")) {
      throw "A runtime S3 bucket still exists."
    }
  }
  foreach ($repository in @($Snapshot.EcrRepositories)) {
    if (-not (Test-AwsResourceAbsent @(
      "ecr", "describe-repositories", "--repository-names", $repository,
      "--region", $script:ExpectedRegion, "--no-cli-pager"
    ) "RepositoryNotFoundException")) {
      throw "A runtime ECR repository still exists."
    }
  }
  if (-not (Test-AwsResourceAbsent @(
    "cloudfront", "get-distribution", "--id", [string]$Snapshot.CloudFrontDistributionId,
    "--no-cli-pager"
  ) "NoSuchDistribution")) {
    throw "The runtime CloudFront distribution still exists."
  }
  if (-not (Test-AwsResourceAbsent @(
    "elbv2", "describe-load-balancers", "--load-balancer-arns", [string]$Snapshot.AlbArn,
    "--region", $script:ExpectedRegion, "--no-cli-pager"
  ) "LoadBalancerNotFound")) {
    throw "The runtime ALB still exists."
  }
  if (-not (Test-AwsResourceAbsent @(
    "rds", "describe-db-instances", "--db-instance-identifier", [string]$Snapshot.RdsIdentifier,
    "--region", $script:ExpectedRegion, "--no-cli-pager"
  ) "DBInstanceNotFound")) {
    throw "The runtime RDS instance still exists."
  }
  foreach ($roleName in @($Snapshot.IamRoleNames)) {
    if (-not (Test-AwsResourceAbsent @(
      "iam", "get-role", "--role-name", $roleName, "--no-cli-pager"
    ) "NoSuchEntity")) {
      throw "A runtime IAM role still exists."
    }
  }
  foreach ($logGroupName in @($Snapshot.LogGroupNames)) {
    $logsJson = Invoke-Native "aws" @(
      "logs", "describe-log-groups",
      "--log-group-name-prefix", $logGroupName,
      "--region", $script:ExpectedRegion,
      "--output", "json",
      "--no-cli-pager"
    ) -Capture
    $matchingLogs = @(
      ($logsJson | ConvertFrom-Json).logGroups |
        Where-Object { $_.logGroupName -eq $logGroupName }
    )
    if ($matchingLogs.Count -gt 0) {
      throw "A runtime CloudWatch log group still exists."
    }
  }

  $clusterJson = Invoke-Native "aws" @(
    "ecs", "describe-clusters", "--clusters", [string]$Snapshot.EcsClusterName,
    "--region", $script:ExpectedRegion, "--output", "json", "--no-cli-pager"
  ) -Capture
  $activeClusters = @(
    ($clusterJson | ConvertFrom-Json).clusters |
      Where-Object { $_.status -eq "ACTIVE" }
  )
  if ($activeClusters.Count -gt 0) {
    throw "The runtime ECS cluster is still active."
  }

  for ($attempt = 1; $attempt -le 12; $attempt++) {
    $taggedJson = Invoke-Native "aws" @(
      "resourcegroupstaggingapi", "get-resources",
      "--tag-filters",
      "Key=Project,Values=$($Snapshot.Project)",
      "Key=Environment,Values=$($Snapshot.Environment)",
      "Key=Lifecycle,Values=runtime",
      "--region", $script:ExpectedRegion,
      "--output", "json",
      "--no-cli-pager"
    ) -Capture
    $tagged = @((($taggedJson | ConvertFrom-Json).ResourceTagMappingList))
    if ($tagged.Count -eq 0) { return }
    if ($attempt -lt 12) { Start-Sleep -Seconds 10 }
  }
  throw "Tagged AWS runtime resources remain after destroy."
}

function Invoke-Down {
  Assert-DestroyRequested ([bool]$ConfirmDestroy) $DestroyConfirmation
  $context = Assert-DemoContext
  Assert-BackendEnvironment
  Initialize-DemoTerraform
  $snapshot = [ordered]@{
    Buckets = @(
      Get-TerraformOutput "documents_bucket_name"
      Get-TerraformOutput "frontend_bucket_name"
    )
    EcrRepositories = @(
      ((Get-TerraformOutput "api_ecr_repository_url") -split "/", 2)[1]
      ((Get-TerraformOutput "worker_ecr_repository_url") -split "/", 2)[1]
    )
    TaskDefinitionFamilies = @(
      Get-TerraformOutput "api_task_definition_family"
      Get-TerraformOutput "worker_task_definition_family"
      Get-TerraformOutput "migration_task_definition_family"
    )
    CloudFrontDistributionId = Get-TerraformOutput "cloudfront_distribution_id"
    EcsClusterName = Get-TerraformOutput "ecs_cluster_name"
    AlbArn = Get-TerraformOutput "alb_arn"
    RdsIdentifier = Get-TerraformOutput "rds_identifier"
    LogGroupNames = @(Get-TerraformJsonOutput "runtime_log_group_names")
    IamRoleNames = @(
      Get-TerraformJsonOutput "runtime_iam_role_arns" |
        ForEach-Object { ([string]$_ -split "/")[-1] }
    )
    Project = if ([string]::IsNullOrWhiteSpace($env:TF_VAR_project)) { "ragproject" } else { $env:TF_VAR_project }
    Environment = if ([string]::IsNullOrWhiteSpace($env:TF_VAR_environment)) { "demo" } else { $env:TF_VAR_environment }
  }
  Write-Step "empty versioned runtime S3 buckets"
  foreach ($bucket in $snapshot.Buckets) { Remove-AllBucketVersions $bucket }
  Push-Location $script:TerraformDirectory
  try {
    Invoke-Native "terraform" @(
      "plan", "-destroy", "-input=false", "-lock-timeout=5m",
      "-out=$script:DestroyPlanPath"
    )
  } finally {
    Pop-Location
  }
  Assert-PlanPreservesBootstrapIdentity $script:DestroyPlanPath
  Write-PlanManifest $script:DestroyPlanPath $script:DestroyManifestPath "runtime-destroy" $context
  Assert-SavedPlan $script:DestroyPlanPath $script:DestroyManifestPath "runtime-destroy" $context
  Write-Step "apply exact destroy plan; bootstrap state and lock resources are outside this stack"
  Apply-SavedPlan $script:DestroyPlanPath
  Write-Step "deregister CI-created task definition revisions"
  Remove-ActiveTaskDefinitions $snapshot.TaskDefinitionFamilies
  Clear-DatabaseUrlSecret
  Write-Step "verify that no runtime resources remain"
  Assert-NoRuntimeRemnants $snapshot
  Write-Host "AWS demo runtime was destroyed and verified."
}

function Invoke-AwsDemo {
  switch ($Command) {
    "doctor" { Invoke-Doctor }
    "plan" { Invoke-Plan }
    "up" { Invoke-Up }
    "load-data" { Invoke-LoadData }
    "smoke" { Invoke-Smoke }
    "status" { Invoke-Status }
    "down" { Invoke-Down }
    default { throw "Unsupported command." }
  }
}

if ($MyInvocation.InvocationName -ne ".") {
  Invoke-AwsDemo
}
