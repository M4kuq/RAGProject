[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-OidcSmokeAccountAllowed {
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

function Get-OidcSmokeRoleDescriptor {
  param([Parameter(Mandatory = $true)][string]$RoleArn)
  $match = [regex]::Match(
    $RoleArn,
    "^arn:aws:iam::(?<account>[0-9]{12}):role/(?<path>[A-Za-z0-9_+=,.@/-]+)$"
  )
  if (-not $match.Success) {
    throw "AWS_OIDC_SMOKE_ROLE_ARN is not a valid IAM role ARN."
  }
  $segments = $match.Groups["path"].Value.Split("/")
  return [pscustomobject]@{
    AccountId = $match.Groups["account"].Value
    RoleName = $segments[-1]
  }
}

function Invoke-OidcSmokeAwsJson {
  $output = & aws sts get-caller-identity --output json --no-cli-pager 2>&1
  $exitCode = $LASTEXITCODE
  $text = (($output | Out-String).Trim())
  if ($exitCode -ne 0) {
    throw "AWS STS verification failed with exit code $exitCode; output is suppressed to protect identifiers."
  }
  try {
    return $text | ConvertFrom-Json
  } catch {
    throw "AWS STS returned invalid JSON; output is suppressed to protect identifiers."
  }
}

function Invoke-OidcSmoke {
  if (-not (Get-Command "aws" -ErrorAction SilentlyContinue)) {
    throw "Required command is unavailable: aws"
  }
  $allowlist = [Environment]::GetEnvironmentVariable("AWS_DEMO_ALLOWED_ACCOUNT_IDS")
  $roleArn = [Environment]::GetEnvironmentVariable("AWS_OIDC_SMOKE_ROLE_ARN")
  if ([string]::IsNullOrWhiteSpace($allowlist) -or [string]::IsNullOrWhiteSpace($roleArn)) {
    throw "AWS_DEMO_ALLOWED_ACCOUNT_IDS and AWS_OIDC_SMOKE_ROLE_ARN are required."
  }

  $expected = Get-OidcSmokeRoleDescriptor $roleArn
  $identity = Invoke-OidcSmokeAwsJson
  $accountId = [string]$identity.Account
  if (
    $accountId -cne $expected.AccountId -or
    -not (Test-OidcSmokeAccountAllowed $accountId $allowlist)
  ) {
    throw "The assumed AWS account did not pass the configured allowlist checks."
  }
  $rolePattern = "^arn:aws:sts::[0-9]{12}:assumed-role/$([regex]::Escape($expected.RoleName))/[^/]+$"
  if ([string]$identity.Arn -notmatch $rolePattern) {
    throw "The caller is not the expected GitHub OIDC smoke role session."
  }
  Write-Host "AWS OIDC smoke passed without printing the AWS account ID or role ARN."
}

if ($MyInvocation.InvocationName -ne ".") {
  Invoke-OidcSmoke
}
