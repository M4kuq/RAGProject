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

$terraformRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$albContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/alb/main.tf") -Raw
$networkContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/network/main.tf") -Raw
$cloudFrontContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/cloudfront/main.tf") -Raw
$rootContent = Get-Content -LiteralPath (Join-Path $terraformRoot "main.tf") -Raw

Assert-True ($albContent -match 'protocol\s+=\s+"HTTPS"') "ALB listener must use HTTPS"
Assert-True ($albContent -match 'port\s+=\s+443') "ALB listener must use port 443"
Assert-True ($albContent -match 'certificate_arn\s+=\s+var\.certificate_arn') "ALB listener must use the supplied ACM certificate"
Assert-True ($albContent -notmatch 'resource\s+"aws_lb_listener"\s+"http"') "ALB must not expose an HTTP listener"
Assert-True ($networkContent -match 'from_port\s+=\s+443') "ALB security group must allow CloudFront on 443"
Assert-True ($cloudFrontContent -match 'origin_protocol_policy\s+=\s+"https-only"') "CloudFront must require HTTPS to the ALB origin"
Assert-True ($cloudFrontContent -match 'domain_name\s+=\s+var\.alb_origin_domain_name') "CloudFront must use the certificate-matching origin domain"
Assert-True ($rootContent -match 'resource\s+"aws_route53_record"\s+"alb_origin"') "runtime must manage the ALB origin alias"
Assert-True ($content -match 'TF_VAR_alb_certificate_arn') "lifecycle must require the ALB certificate ARN"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $terraformRoot "../.."))
$lifecycleWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-demo.yml") -Raw
$planWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-infra-plan.yml") -Raw
Assert-True ($lifecycleWorkflow -notmatch '\\\$\{\{') "GitHub workflow expressions must not contain literal backslashes"
Assert-True ($planWorkflow -match "github\.ref == 'refs/heads/deploy/AWS_ECS'") "manual Terraform plan must be restricted to deploy/AWS_ECS"

Write-Host "aws-demo parser and credential-free tests passed."
