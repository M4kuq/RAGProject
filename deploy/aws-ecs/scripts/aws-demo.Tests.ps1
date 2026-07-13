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
$iamContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/iam/main.tf") -Raw

Assert-True ($albContent -match 'protocol\s+=\s+"HTTPS"') "ALB listener must use HTTPS"
Assert-True ($albContent -match 'port\s+=\s+443') "ALB listener must use port 443"
Assert-True ($albContent -match 'certificate_arn\s+=\s+var\.certificate_arn') "ALB listener must use the supplied ACM certificate"
Assert-True ($albContent -notmatch 'resource\s+"aws_lb_listener"\s+"http"') "ALB must not expose an HTTP listener"
Assert-True ($networkContent -match 'from_port\s+=\s+443') "ALB security group must allow CloudFront on 443"
Assert-True ($cloudFrontContent -match 'origin_protocol_policy\s+=\s+"https-only"') "CloudFront must require HTTPS to the ALB origin"
Assert-True ($cloudFrontContent -match 'domain_name\s+=\s+var\.alb_origin_domain_name') "CloudFront must use the certificate-matching origin domain"
Assert-True ($cloudFrontContent -match 'header_behavior\s+=\s+"allExcept"') "API origin policy must exclude selected viewer headers"
Assert-True ($cloudFrontContent -match 'items\s+=\s+\["Host"\]') "API origin policy must not forward the viewer Host"
Assert-True ($cloudFrontContent -notmatch 'headers\s+=\s+\["\*"\]') "API behavior must not forward every viewer header"
Assert-True ($rootContent -match 'resource\s+"aws_route53_record"\s+"alb_origin"') "runtime must manage the ALB origin alias"
Assert-True ($content -match 'TF_VAR_alb_certificate_arn') "lifecycle must require the ALB certificate ARN"

$ecrContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/ecr/main.tf") -Raw
$providerContent = Get-Content -LiteralPath (Join-Path $terraformRoot "providers.tf") -Raw
Assert-True ($ecrContent -match 'force_delete\s+=\s+true') "runtime ECR repositories must be removable after image pushes"
Assert-True ($providerContent -match 'Lifecycle\s+=\s+"runtime"') "runtime resources need a teardown-only tag"
Assert-True ($content.Contains('"-var=api_image_tag=$ApiImageTag"')) "scale plan must keep the deployed API image tag"
Assert-True ($content.Contains('"-var=worker_image_tag=$WorkerImageTag"')) "scale plan must keep the deployed worker image tag"
Assert-True ($content -match 'Remove-ActiveTaskDefinitions') "down must deregister CI-created task definitions"
Assert-True ($content -match 'for \(\$attempt = 1; \$attempt -le 7; \$attempt\+\+\)') "task definition cleanup must include a final verification pass"
Assert-True ($content -match 'if \(\$attempt -eq 7\) \{ break \}') "the final cleanup pass must only verify convergence"
Assert-True ($content -match 'Smoke search returned no results') "smoke must fail on an empty retrieval result"
Assert-True ($content -match 'Get-TerraformOutput "database_name"') "database URL must use the configured database name"
Assert-True (($content -split 'source_sha = \$context\.GitSha').Count -eq 3) "both deploy workflows must receive the exact planned commit"
Assert-True ($content -match 'Key=Lifecycle,Values=runtime') "remnant checks must exclude persistent bootstrap resources"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $terraformRoot "../.."))
$lifecycleWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-demo.yml") -Raw
$appWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-deploy-app.yml") -Raw
$frontendWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-deploy-frontend.yml") -Raw
$planWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-infra-plan.yml") -Raw
Assert-True ($lifecycleWorkflow -notmatch '\\\$\{\{') "GitHub workflow expressions must not contain literal backslashes"
Assert-True ($appWorkflow -notmatch '\\\$\{\{') "app workflow expressions must not contain literal backslashes"
Assert-True ($frontendWorkflow -notmatch '\\\$\{\{') "frontend workflow expressions must not contain literal backslashes"
Assert-True ($appWorkflow -match 'group: aws-demo-runtime-deploy-AWS_ECS') "app deploy must share the runtime teardown lock"
Assert-True ($frontendWorkflow -match 'group: aws-demo-runtime-deploy-AWS_ECS') "frontend deploy must share the runtime teardown lock"
Assert-True ($lifecycleWorkflow -match 'aws-demo-up-orchestrator-deploy-AWS_ECS') "up orchestration must avoid deadlocking dispatched deploy workflows"
Assert-True ($lifecycleWorkflow -match 'Another AWS Demo up/down run is active') "up and down must reject conflicting lifecycle runs"
Assert-True ($lifecycleWorkflow -match 'select\(\.status != "completed"\)') "lifecycle conflict detection must include queued and waiting runs"
Assert-True ($appWorkflow -match 'git merge-base --is-ancestor "\$SOURCE_SHA" "origin/deploy/AWS_ECS"') "app workflow must restrict source_sha to the deploy branch history"
Assert-True ($frontendWorkflow -match 'git merge-base --is-ancestor "\$SOURCE_SHA" "origin/deploy/AWS_ECS"') "frontend workflow must restrict source_sha to the deploy branch history"
Assert-True ($appWorkflow -match 'git checkout --detach "\$SOURCE_SHA"') "app workflow must checkout the validated planned commit"
Assert-True ($frontendWorkflow -match 'git checkout --detach "\$SOURCE_SHA"') "frontend workflow must checkout the validated planned commit"
Assert-True ($rootContent -match 'RAG_DEMO_ADMIN_PASSWORD\s+=\s+var\.demo_admin_password_secret_arn') "migration must receive the deployed admin password from Secrets Manager"
Assert-True (($iamContent -split 'secretsmanager:GetSecretValue').Count -eq 2) "only the ECS task execution role may read configured Secrets Manager values"
Assert-True ($iamContent -match 'local\.bedrock_rerank_model_arn') "rerank model ARN must be included in bedrock InvokeModel resources"
Assert-True ($iamContent -match 'actions\s+=\s+\["s3:ListBucket"\]') "missing-object checks need bucket-level ListBucket"
Assert-True ($iamContent -match 'values\s+=\s+\["\$\{var\.documents_key_prefix\}/\*"\]') "ListBucket must be constrained to the documents prefix"
Assert-True ($rootContent -match 'GENERATION_MAX_OUTPUT_TOKENS\s+=\s+"5000"') "Nova Lite generation must stay within its 5K output limit"
Assert-True ($planWorkflow -match "github\.ref == 'refs/heads/deploy/AWS_ECS'") "manual Terraform plan must be restricted to deploy/AWS_ECS"

Write-Host "aws-demo parser and credential-free tests passed."
