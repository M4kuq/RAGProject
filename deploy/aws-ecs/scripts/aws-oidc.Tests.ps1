[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-OidcTestTrue {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Assert-OidcTestParses {
  param([Parameter(Mandatory = $true)][string]$Path)
  $tokens = $null
  $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile(
    $Path,
    [ref]$tokens,
    [ref]$errors
  ) | Out-Null
  if ($errors.Count -gt 0) {
    $messages = $errors | ForEach-Object { $_.Message }
    throw "PowerShell parser errors in $Path`: $($messages -join '; ')"
  }
}

$bootstrapPath = Join-Path $PSScriptRoot "aws-oidc-bootstrap.ps1"
$smokePath = Join-Path $PSScriptRoot "aws-oidc-smoke.ps1"
Assert-OidcTestParses $bootstrapPath
Assert-OidcTestParses $smokePath

. $bootstrapPath -Repository "M4kuq/RAGProject"
. $smokePath

$providerArn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
$policy = New-OidcBootstrapTrustPolicy $providerArn "M4kuq/RAGProject" "deploy/AWS_ECS"
Assert-OidcTestTrue (
  Test-OidcBootstrapTrustPolicy $policy $providerArn "M4kuq/RAGProject" "deploy/AWS_ECS"
) "generated trust policy must match the exact repository and branch"
Assert-OidcTestTrue (-not (
  Test-OidcBootstrapTrustPolicy $policy $providerArn "M4kuq/RAGProject" "main"
)) "generated trust policy must reject another branch"

Assert-OidcBootstrapConfirmation "CREATE-GITHUB-OIDC-SMOKE"
$confirmationRejected = $false
try {
  Assert-OidcBootstrapConfirmation "create-github-oidc-smoke"
} catch {
  $confirmationRejected = $true
}
Assert-OidcTestTrue $confirmationRejected "bootstrap apply confirmation must be case-sensitive"

Assert-OidcTestTrue (
  Test-OidcSmokeAccountAllowed "123456789012" "111111111111,123456789012"
) "smoke allowlist must accept an exact account"
Assert-OidcTestTrue (-not (
  Test-OidcSmokeAccountAllowed "123456789012" "111111111111"
)) "smoke allowlist must reject another account"
$role = Get-OidcSmokeRoleDescriptor "arn:aws:iam::123456789012:role/path/ragproject-demo-github-oidc-smoke"
Assert-OidcTestTrue ($role.RoleName -ceq "ragproject-demo-github-oidc-smoke") "role path parsing must keep the final role name"

$script:OidcMockAwsCalls = [System.Collections.Generic.List[string]]::new()
function aws {
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments)
  $call = ($Arguments | ForEach-Object { [string]$_ }) -join " "
  $script:OidcMockAwsCalls.Add($call)
  $global:LASTEXITCODE = 0
  if ($call -match '^sts get-caller-identity ') {
    return '{"Account":"123456789012","Arn":"arn:aws:sts::123456789012:assumed-role/local/test"}'
  }
  if ($call -match '^iam list-open-id-connect-providers ') {
    return '{"OpenIDConnectProviderList":[]}'
  }
  if ($call -match '^iam get-role ') {
    $global:LASTEXITCODE = 254
    return "NoSuchEntity"
  }
  throw "Unexpected mocked AWS CLI call."
}
[Environment]::SetEnvironmentVariable("AWS_DEMO_ALLOWED_ACCOUNT_IDS", "123456789012", "Process")
$Command = "plan"
Invoke-OidcBootstrap
Assert-OidcTestTrue (-not ($script:OidcMockAwsCalls -match 'create-open-id-connect-provider|create-role')) "bootstrap plan must not make mutating AWS calls"
Remove-Item Function:\aws

$bootstrapContent = Get-Content -LiteralPath $bootstrapPath -Raw
$smokeContent = Get-Content -LiteralPath $smokePath -Raw
Assert-OidcTestTrue ($bootstrapContent -match 'ValidateSet\("plan", "apply"\)') "bootstrap must default to a reviewable plan/apply split"
Assert-OidcTestTrue ($bootstrapContent -match 'CREATE-GITHUB-OIDC-SMOKE') "bootstrap apply must require the exact confirmation"
Assert-OidcTestTrue ($bootstrapContent -notmatch 'attach-role-policy|put-role-policy') "smoke role must not receive permission policies"
Assert-OidcTestTrue ($bootstrapContent -notmatch 'aws\s+configure|--debug|export-credentials') "bootstrap must not export or debug credentials"
Assert-OidcTestTrue ($bootstrapContent -match 'finally\s*\{[\s\S]*Remove-Item -LiteralPath \$policyPath') "temporary trust policy must always be removed"
Assert-OidcTestTrue ($smokeContent -notmatch 'Write-Host\s+"Account:|Write-Host[^\r\n]*\$(identity|roleArn|accountId|expected)') "smoke must not print account IDs or ARNs"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../../.."))
$workflowPath = Join-Path $repoRoot ".github/workflows/aws-oidc-smoke.yml"
$workflow = Get-Content -LiteralPath $workflowPath -Raw
Assert-OidcTestTrue ($workflow -match 'workflow_dispatch:') "OIDC smoke must be manually dispatched"
Assert-OidcTestTrue ($workflow -match "github\.ref == 'refs/heads/deploy/AWS_ECS'") "OIDC smoke must be restricted to deploy/AWS_ECS"
Assert-OidcTestTrue ($workflow -match 'id-token:\s*write') "OIDC smoke must request the ID token permission"
Assert-OidcTestTrue ($workflow -match 'AWS_OIDC_SMOKE_ROLE_ARN') "OIDC smoke must use its permissionless role variable"
Assert-OidcTestTrue ($workflow -match 'allowed-account-ids:') "OIDC smoke must restrict the expected account"
Assert-OidcTestTrue ($workflow -match 'mask-aws-account-id:\s*true') "OIDC smoke must mask the account ID"
Assert-OidcTestTrue ($workflow -match 'unset-current-credentials:\s*true') "OIDC smoke must discard inherited credentials"
Assert-OidcTestTrue ($workflow -match 'actions/checkout@v5') "OIDC smoke checkout must use the Node 24 action"
Assert-OidcTestTrue ($workflow -match 'aws-actions/configure-aws-credentials@v6\.1\.0') "OIDC smoke credentials must support account allowlisting on Node 24"
Assert-OidcTestTrue ($workflow -match 'aws-oidc-smoke\.ps1') "OIDC smoke must run the redacted verifier"
Assert-OidcTestTrue ($workflow -notmatch '\$\{\{\s*secrets\.') "OIDC smoke must not load repository secrets"
Assert-OidcTestTrue ($workflow -notmatch 'terraform|\bapply\b|\bdestroy\b|create-open-id-connect-provider|create-role') "OIDC smoke must not mutate AWS or run Terraform"

$demoScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot "aws-demo.ps1") -Raw
Assert-OidcTestTrue ($demoScript -notmatch 'Write-Host\s+"Account:') "lifecycle commands must not print account IDs"
foreach ($name in @(
  "aws-demo.yml",
  "aws-deploy-app.yml",
  "aws-deploy-frontend.yml",
  "aws-infra-plan.yml",
  "aws-oidc-smoke.yml"
)) {
  $content = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/$name") -Raw
  Assert-OidcTestTrue ($content -match 'mask-aws-account-id:\s*true') "$name must mask the AWS account ID"
}

Write-Host "AWS OIDC parser and credential-free tests passed."
