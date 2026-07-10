[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "aws-demo.ps1"
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  $scriptPath,
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {
  $messages = $errors | ForEach-Object { $_.Message }
  throw "PowerShell parser errors: $($messages -join '; ')"
}

. $scriptPath -Command doctor

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

Assert-True (Test-DemoAccountAllowed "123456789012" "111111111111, 123456789012") "allowlisted account must pass"
Assert-True (-not (Test-DemoAccountAllowed "123456789012" "111111111111")) "non-allowlisted account must fail"
Assert-True (-not (Test-DemoAccountAllowed "invalid" "invalid")) "malformed account ids must fail"

Assert-DestroyRequested $true "DESTROY-RUNTIME"
$destroyRejected = $false
try {
  Assert-DestroyRequested $true "destroy-runtime"
} catch {
  $destroyRejected = $true
}
Assert-True $destroyRejected "destroy phrase must be case-sensitive"

$content = Get-Content -LiteralPath $scriptPath -Raw
Assert-True ($content -notmatch 'terraform\s+destroy') "direct terraform destroy is forbidden"
Assert-True ($content -match '"plan", "-destroy"') "destroy must create a saved plan"
Assert-True ($content -match 'Apply-SavedPlan') "apply must use the saved-plan helper"
Assert-True ($content -match 'AWS_DEMO_ALLOWED_ACCOUNT_IDS') "sandbox allowlist is required"
Assert-True ($content -match 'deploy/AWS_ECS') "the long-lived branch guard is required"
Assert-True ($content -match 'ap-northeast-1') "the region guard is required"
Assert-True ($content -match 'Remove-AllBucketVersions') "versioned S3 cleanup is required"
Assert-True ($content -match 'Assert-NoRuntimeRemnants') "post-destroy verification is required"

Write-Host "aws-demo parser and credential-free tests passed."
