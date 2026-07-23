param(
  [string[]]$Models = @(
    "nvidia/llama-3.3-nemotron-super-49b-v1.5"
  )
)

function Get-LocalEnvValue {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  $envPath = Join-Path $PSScriptRoot "..\.env"
  if (-not (Test-Path -LiteralPath $envPath)) {
    return $null
  }

  $escapedName = [Regex]::Escape($Name)
  foreach ($line in Get-Content -LiteralPath $envPath) {
    if ($line -match "^\s*$escapedName\s*=\s*(.*)\s*$") {
      $value = $Matches[1].Trim()
      if ($value.Length -ge 2) {
        $isDoubleQuoted = $value.StartsWith('"') -and $value.EndsWith('"')
        $isSingleQuoted = $value.StartsWith("'") -and $value.EndsWith("'")
        if ($isDoubleQuoted -or $isSingleQuoted) {
          $value = $value.Substring(1, $value.Length - 2)
        }
      }
      return $value
    }
  }

  return $null
}

$apiKey = $env:NVIDIA_API_KEY
if ([string]::IsNullOrWhiteSpace($apiKey)) {
  $apiKey = Get-LocalEnvValue -Name "NVIDIA_API_KEY"
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
  Write-Error "NVIDIA_API_KEY is required in the repository root .env file or current process."
  exit 1
}
$env:NVIDIA_API_KEY = $apiKey

$baseUrl = $env:NVIDIA_BASE_URL
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
  $baseUrl = Get-LocalEnvValue -Name "NVIDIA_BASE_URL"
}
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
  $baseUrl = "https://integrate.api.nvidia.com/v1"
}

$timeoutSeconds = $env:NVIDIA_TIMEOUT_SECONDS
if ([string]::IsNullOrWhiteSpace($timeoutSeconds)) {
  $timeoutSeconds = Get-LocalEnvValue -Name "NVIDIA_TIMEOUT_SECONDS"
}
if ([string]::IsNullOrWhiteSpace($timeoutSeconds)) {
  $timeoutSeconds = "60"
}

docker compose -f docker-compose.ci.yml build backend-test
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

foreach ($model in $Models) {
  if ([string]::IsNullOrWhiteSpace($model)) {
    Write-Error "Every NVIDIA model ID must be non-empty."
    exit 1
  }

  Write-Host "Testing NVIDIA catalog model: $model"
  docker run --rm `
    -e RUN_NVIDIA_GENERATION_TEST=true `
    -e NVIDIA_API_KEY `
    -e NVIDIA_MODEL_NAME=$model `
    -e NVIDIA_BASE_URL=$baseUrl `
    -e NVIDIA_TIMEOUT_SECONDS=$timeoutSeconds `
    ragproject-ci-backend-test `
    pytest tests/test_nvidia_generation.py -k real_api -q

  if ($LASTEXITCODE -ne 0) {
    Write-Error (
      "NVIDIA generation check failed for $model. " +
      "For 404 or retirement errors, recheck Free Endpoint status on build.nvidia.com."
    )
    exit $LASTEXITCODE
  }
}

Write-Host "NVIDIA generation checks passed."
exit 0
