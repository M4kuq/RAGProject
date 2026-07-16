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
$tagTypeFixture = [pscustomobject]@{ ResourceARN = "arn:aws:ecs:ap-northeast-1:000000000000:service/demo/service" }
Assert-True ((Get-TaggedRuntimeResourceType $tagTypeFixture) -eq "ecs/service") "tag remnant types must omit identifiers"
$unknownTagTypeFixture = [pscustomobject]@{ ResourceARN = "malformed" }
Assert-True ((Get-TaggedRuntimeResourceType $unknownTagTypeFixture) -eq "unknown/unknown") "malformed tag remnants must remain unverified"

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
Assert-True ($content -match 'PSObject\.Properties\["resource_changes"\]') "empty Terraform plans must omit resource_changes safely"
Assert-True ($content -match 'for \(\$attempt = 1; \$attempt -le 30; \$attempt\+\+\)') "tag remnant verification must tolerate AWS tag-index convergence"
Assert-True ($content -match 'Active or unverified runtime resource types still visible') "tag remnant failures must report only resource types"
Assert-True ($content -match 'Test-TaggedRuntimeResourceInactive') "tag remnants must use authoritative service checks"
Assert-True ($content -match 'InvalidSubnetID\.NotFound') "subnet tombstones must be verified with EC2"
Assert-True ($content -match 'InvalidSecurityGroupRuleId\.NotFound') "security-group-rule tombstones must be verified with EC2"
Assert-True ($content -match 'Group-Object\s+\|\s+Sort-Object Name') "tag remnant type diagnostics must aggregate identifiers"
Assert-True ($content -match 'AWS_DEMO_ALLOWED_ACCOUNT_IDS') "sandbox allowlist is required"
Assert-True ($content -match 'deploy/AWS_ECS') "the long-lived branch guard is required"
Assert-True ($content -match 'ap-northeast-1') "the region guard is required"
Assert-True ($content -match 'Remove-AllBucketVersions') "versioned S3 cleanup is required"
Assert-True ($content -match 'Assert-NoRuntimeRemnants') "post-destroy verification is required"
Assert-True ($content -match 'Get-OptionalTerraformOutput') "down must tolerate outputs missing after a partial apply"
Assert-True ($content -match 'No outputs found') "optional outputs must only ignore Terraform missing-output errors"

$terraformRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$albContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/alb/main.tf") -Raw
$networkContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/network/main.tf") -Raw
$cloudFrontContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/cloudfront/main.tf") -Raw
$rootContent = Get-Content -LiteralPath (Join-Path $terraformRoot "main.tf") -Raw
$iamContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/iam/main.tf") -Raw

Assert-True ($albContent -match 'internal\s+=\s+true') "ALB must be internal"
Assert-True ($albContent -match 'resource\s+"aws_lb_listener"\s+"http"') "internal ALB must use an HTTP listener"
Assert-True ($albContent -match 'protocol\s+=\s+"HTTP"') "internal ALB listener must use HTTP"
Assert-True ($albContent -match 'port\s+=\s+80') "internal ALB listener must use port 80"
Assert-True ($albContent -notmatch 'certificate_arn') "internal ALB must not require an ACM certificate"
Assert-True ($networkContent -match 'resource\s+"aws_subnet"\s+"private"') "VPC origin requires private subnets"
Assert-True ($networkContent -match 'zone_ids\[index\]\s+!=\s+"apne1-az3"') "unsupported Tokyo VPC origin AZ must be excluded"
Assert-True ($networkContent -match 'resource\s+"aws_route_table"\s+"private"') "private ALB subnets need an explicit route table"
Assert-True ($networkContent -match 'from_port\s+=\s+80') "ALB security group must allow CloudFront VPC origin on port 80"
Assert-True ($cloudFrontContent -match 'resource\s+"aws_cloudfront_vpc_origin"\s+"api"') "CloudFront must create a VPC origin"
Assert-True ($cloudFrontContent -match 'origin_protocol_policy\s+=\s+"http-only"') "CloudFront VPC origin must use private HTTP"
Assert-True ($cloudFrontContent -match 'vpc_origin_config') "CloudFront distribution must attach the VPC origin"
Assert-True ($cloudFrontContent -match 'delete\s+=\s+"30m"') "VPC origin deletion needs an explicit timeout"
Assert-True ($cloudFrontContent -match 'domain_name\s+=\s+var\.alb_dns_name') "CloudFront must use the internal ALB DNS name"
Assert-True ($cloudFrontContent -match 'header_behavior\s+=\s+"allExcept"') "API origin policy must exclude selected viewer headers"
Assert-True ($cloudFrontContent -match 'items\s+=\s+\["Host"\]') "API origin policy must not forward the viewer Host"
Assert-True ($cloudFrontContent -notmatch 'headers\s+=\s+\["\*"\]') "API behavior must not forward every viewer header"
Assert-True ($cloudFrontContent -match 'Managed-CachingDisabled') "API behavior must use the managed disabled-cache policy"
Assert-True ($cloudFrontContent -match 'cache_policy_id\s+=\s+data\.aws_cloudfront_cache_policy\.api_disabled\.id') "API origin request policy requires an explicit cache policy"
Assert-True ($rootContent -notmatch 'resource\s+"aws_route53_record"') "default-domain deployment must not require Route 53"
Assert-True ($content -notmatch 'TF_VAR_alb_certificate_arn') "lifecycle must not require an ALB certificate"
Assert-True ($content -match 'get-vpc-origin') "destroy verification must check the CloudFront VPC origin"

$ecrContent = Get-Content -LiteralPath (Join-Path $terraformRoot "modules/ecr/main.tf") -Raw
$providerContent = Get-Content -LiteralPath (Join-Path $terraformRoot "providers.tf") -Raw
Assert-True ($ecrContent -match 'force_delete\s+=\s+true') "runtime ECR repositories must be removable after image pushes"
Assert-True ($providerContent -match 'Lifecycle\s+=\s+"runtime"') "runtime resources need a teardown-only tag"
Assert-True ($content.Contains('"-var=api_image_tag=$ApiImageTag"')) "scale plan must keep the deployed API image tag"
Assert-True ($content.Contains('"-var=worker_image_tag=$WorkerImageTag"')) "scale plan must keep the deployed worker image tag"
Assert-True ($content -match 'Remove-ActiveTaskDefinitions') "down must deregister CI-created task definitions"
Assert-True ($content -match 'for \(\$attempt = 1; \$attempt -le 7; \$attempt\+\+\)') "task definition cleanup must include a final verification pass"
Assert-True ($content -match 'if \(\$attempt -eq 7\) \{ break \}') "the final cleanup pass must only verify convergence"
Assert-True ($content -match 'RAG_DEMO_REQUIRE_SEARCH_RESULTS') "smoke must expose an explicit no-data result policy"
Assert-True ($content -match 'function Invoke-SmokeReady') "smoke must wait for the newly deployed endpoint"
Assert-True ($content -match '\$statusCode -in @\(502, 503, 504\)') "smoke readiness retries must be limited to transient gateway failures"
Assert-True ($content -match '\[int\]\$MaxAttempts = 12') "smoke readiness retries must remain bounded"
Assert-True ($content -match 'Smoke readiness endpoint did not become available') "smoke readiness failures must not expose the endpoint"
Assert-True ($content -match '\$requireSearchResults\s+-and\s+\$resultCount\s+-le\s+0') "smoke must only require retrieval results when requested"
Assert-True ($content -match 'Smoke search returned no results') "strict smoke must fail on an empty retrieval result"
Assert-True ($content -match 'Get-TerraformOutput "database_name"') "database URL must use the configured database name"
Assert-True (($content -split 'source_sha = \$context\.GitSha').Count -eq 3) "both deploy workflows must receive the exact planned commit"
Assert-True ($content -match 'Update-DeploymentConfigSecret') "fresh Terraform outputs must be stored in the runtime deployment config secret"
Assert-True ($content -notmatch 'deployment_config = \$') "workflow dispatch metadata must not contain deployment identifiers"
Assert-True ($content -match 'Key=Lifecycle,Values=runtime') "remnant checks must exclude persistent bootstrap resources"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $terraformRoot "../.."))
$lifecycleWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-demo.yml") -Raw
$appWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-deploy-app.yml") -Raw
$frontendWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-deploy-frontend.yml") -Raw
$planWorkflow = Get-Content -LiteralPath (Join-Path $repoRoot ".github/workflows/aws-infra-plan.yml") -Raw
$providerLock = Get-Content -LiteralPath (Join-Path $terraformRoot ".terraform.lock.hcl") -Raw
$bootstrapAccess = Get-Content -LiteralPath (Join-Path $repoRoot "deploy/aws-ecs/bootstrap/access.tf") -Raw
$bootstrapLifecycle = Get-Content -LiteralPath (Join-Path $repoRoot "deploy/aws-ecs/bootstrap/lifecycle.tf") -Raw
$bootstrapSecrets = Get-Content -LiteralPath (Join-Path $repoRoot "deploy/aws-ecs/bootstrap/secrets.tf") -Raw
Assert-True ($lifecycleWorkflow -match 'secrets\.AWS_GITHUB_OIDC_PROVIDER_ARN') "lifecycle workflow must use a GitHub-compatible OIDC provider secret name"
Assert-True ($lifecycleWorkflow -match 'require_search_results:') "lifecycle workflow must expose the no-data smoke switch"
Assert-True ($lifecycleWorkflow -match 'RAG_DEMO_REQUIRE_SEARCH_RESULTS:\s+\$\{\{ inputs\.require_search_results \}\}') "lifecycle workflow must pass the no-data smoke switch to the script"
Assert-True ($planWorkflow -match 'secrets\.AWS_GITHUB_OIDC_PROVIDER_ARN') "plan workflow must use a GitHub-compatible OIDC provider secret name"
Assert-True ($lifecycleWorkflow -notmatch 'secrets\.GITHUB_') "lifecycle workflow must not use the reserved GITHUB_ secret prefix"
Assert-True ($planWorkflow -notmatch 'secrets\.GITHUB_') "plan workflow must not use the reserved GITHUB_ secret prefix"
Assert-True (($providerLock -split '"h1:').Count -ge 3) "provider lock must include checksums for Windows and Linux runners"
Assert-True ($bootstrapAccess -match 'iam::aws:policy/ReadOnlyAccess') "Terraform plan role must use the AWS read-only managed policy"
Assert-True ($bootstrapAccess -match '"dynamodb:PutItem"') "Terraform plan role must be able to acquire the state lock"
Assert-True ($bootstrapAccess -match '"dynamodb:DeleteItem"') "Terraform plan role must be able to release the state lock"
Assert-True ($bootstrapAccess -notmatch 'AdministratorAccess|iam:PassRole|secretsmanager:GetSecretValue') "Terraform plan role must not gain lifecycle or secret-value permissions"
Assert-True ($bootstrapLifecycle -match 'sid\s+=\s+"ManageRuntimeCloudMapHostedZone"') "Cloud Map hosted-zone permissions must be isolated from regional service-discovery permissions"
Assert-True ($bootstrapLifecycle -match '"route53:CreateHostedZone"') "Cloud Map private namespace creation requires Route 53 hosted-zone creation"
Assert-True ($bootstrapLifecycle -match '"route53:GetHostedZone"') "Cloud Map private namespace creation requires Route 53 hosted-zone reads"
Assert-True ($bootstrapLifecycle -match '"route53:ListHostedZonesByName"') "Cloud Map private namespace creation requires Route 53 hosted-zone lookup"
Assert-True ($bootstrapLifecycle -match '"route53:DeleteHostedZone"') "Cloud Map private namespace cleanup requires Route 53 hosted-zone deletion"
Assert-True ($bootstrapLifecycle -notmatch 'AdministratorAccess|PowerUserAccess|iam:CreateUser|iam:CreateAccessKey|ec2:RunInstances') "Terraform lifecycle role must not gain administrator, IAM-user, access-key, or EC2-instance permissions"
Assert-True ($bootstrapLifecycle -match 'PassOnlyRuntimeEcsRoles') "Terraform lifecycle role must scope iam:PassRole to runtime ECS roles"
Assert-True ($bootstrapLifecycle -match 'ecs-tasks\.amazonaws\.com' -and $bootstrapLifecycle -match 'ecs\.amazonaws\.com') "Terraform lifecycle role must restrict passed roles to ECS services"
Assert-True ($bootstrapLifecycle -match 'vpcorigin\.cloudfront\.amazonaws\.com') "Terraform lifecycle role must allow CloudFront to create its VPC origin service-linked role"
Assert-True ($bootstrapLifecycle -match 'budgets:TagResource') "Terraform lifecycle role must be able to tag the scoped runtime budget"
Assert-True ($bootstrapLifecycle -match 'CreateOnlyRdsManagedMasterSecret') "Terraform lifecycle role must scope RDS managed-secret creation"
Assert-True ($bootstrapLifecycle -match 'secretsmanager:CreateSecret' -and $bootstrapLifecycle -match 'secretsmanager:TagResource') "RDS managed master passwords require secret create and tag permissions"
Assert-True (($bootstrapLifecycle -split 'secretsmanager:GetSecretValue').Count -eq 2) "Terraform lifecycle role may read only the RDS-managed master secret pattern"
Assert-True ($bootstrapLifecycle -match 'secret:rds!db-\*') "Terraform lifecycle role must scope secret-value reads to RDS-managed master secrets"
Assert-True ($bootstrapLifecycle -match 'aws_secretsmanager_secret\.input\["database_url"\]\.arn') "Terraform lifecycle role must scope DATABASE_URL writes to the bootstrap secret ARN"
Assert-True ($bootstrapLifecycle -notmatch 'iam:CreateOpenIDConnectProvider|iam:DeleteOpenIDConnectProvider|iam:UpdateOpenIDConnectProviderThumbprint') "Terraform lifecycle role must not mutate the bootstrap OIDC provider"
Assert-True ($bootstrapLifecycle -match 'prevent_destroy\s*=\s*true') "Terraform lifecycle role and policies must be protected from destroy"
Assert-True ($bootstrapSecrets -notmatch 'aws_secretsmanager_secret_version|secret_string') "bootstrap must create secret containers without secret values"
Assert-True (($bootstrapSecrets -split 'prevent_destroy\s*=\s*true').Count -eq 2) "bootstrap input secret containers must be protected from destroy"
Assert-True ($lifecycleWorkflow -notmatch '\\\$\{\{') "GitHub workflow expressions must not contain literal backslashes"
Assert-True ($appWorkflow -notmatch '\\\$\{\{') "app workflow expressions must not contain literal backslashes"
Assert-True ($frontendWorkflow -notmatch '\\\$\{\{') "frontend workflow expressions must not contain literal backslashes"
Assert-True ($appWorkflow -match 'group: aws-demo-runtime-deploy-AWS_ECS') "app deploy must share the runtime teardown lock"
Assert-True ($frontendWorkflow -match 'group: aws-demo-runtime-deploy-AWS_ECS') "frontend deploy must share the runtime teardown lock"
Assert-True ($lifecycleWorkflow -match 'aws-demo-up-orchestrator-deploy-AWS_ECS') "up orchestration must avoid deadlocking dispatched deploy workflows"
Assert-True ($lifecycleWorkflow -match 'Another AWS Demo up/down run is active') "up and down must reject conflicting lifecycle runs"
Assert-True ($lifecycleWorkflow -match 'select\(\.status != "completed"\)') "lifecycle conflict detection must include queued and waiting runs"
Assert-True ($lifecycleWorkflow.Contains('--repo "$GITHUB_REPOSITORY"')) "pre-checkout lifecycle conflict detection must identify the repository explicitly"
Assert-True ($appWorkflow -match 'git merge-base --is-ancestor "\$SOURCE_SHA" "origin/deploy/AWS_ECS"') "app workflow must restrict source_sha to the deploy branch history"
Assert-True ($frontendWorkflow -match 'git merge-base --is-ancestor "\$SOURCE_SHA" "origin/deploy/AWS_ECS"') "frontend workflow must restrict source_sha to the deploy branch history"
Assert-True ($appWorkflow -match 'git checkout --detach "\$SOURCE_SHA"') "app workflow must checkout the validated planned commit"
Assert-True ($frontendWorkflow -match 'git checkout --detach "\$SOURCE_SHA"') "frontend workflow must checkout the validated planned commit"
Assert-True ($appWorkflow -match 'role-to-assume: arn:aws:iam::\$\{\{ secrets\.AWS_DEMO_ACCOUNT_ID \}\}:role/ragproject-demo-github-deploy') "app workflow must derive the fixed deploy role from a masked account secret"
Assert-True ($frontendWorkflow -match 'role-to-assume: arn:aws:iam::\$\{\{ secrets\.AWS_DEMO_ACCOUNT_ID \}\}:role/ragproject-demo-github-deploy') "frontend workflow must derive the fixed deploy role from a masked account secret"
Assert-True ($appWorkflow -match 'secretsmanager get-secret-value') "app workflow must load deployment identifiers from Secrets Manager"
Assert-True ($frontendWorkflow -match 'secretsmanager get-secret-value') "frontend workflow must load deployment identifiers from Secrets Manager"
Assert-True ($appWorkflow -notmatch 'inputs\.deployment_config') "app workflow dispatch metadata must not carry deployment identifiers"
Assert-True ($frontendWorkflow -notmatch 'inputs\.deployment_config') "frontend workflow dispatch metadata must not carry deployment identifiers"
Assert-True ($appWorkflow -match 'allowed-account-ids: \$\{\{ secrets\.AWS_DEMO_ALLOWED_ACCOUNT_IDS \}\}') "app credentials must enforce the account allowlist"
Assert-True ($frontendWorkflow -match 'allowed-account-ids: \$\{\{ secrets\.AWS_DEMO_ALLOWED_ACCOUNT_IDS \}\}') "frontend credentials must enforce the account allowlist"
Assert-True ($appWorkflow -match 'startswith\(\$account \+ "\.dkr\.ecr\.ap-northeast-1\.amazonaws\.com/"\)') "ECR deployment config must match the allowed account"
Assert-True ($iamContent -match 'sid\s+=\s+"ReadMigrationTaskLogs"') "deploy role must isolate migration log reads"
Assert-True ($iamContent -match '"logs:GetLogEvents"') "deploy role must be able to read failed migration logs"
Assert-True ($iamContent -match 'log-stream:migration/migration/\*') "migration log reads must be scoped to migration streams"
Assert-True ($appWorkflow -match 'aws logs get-log-events') "app workflow must retrieve failed migration logs"
Assert-True ($appWorkflow -match 'Migration container logs') "app workflow must group migration diagnostics"
Assert-True ($appWorkflow.Contains('(DATABASE_URL|RAG_DEMO_ADMIN_PASSWORD|password)=')) "migration diagnostics must redact known secret assignments"
Assert-True ($appWorkflow -match 'actions/checkout@v6') "app checkout must use the Node 24 action"
Assert-True ($frontendWorkflow -match 'actions/setup-node@v6') "frontend setup-node must use the Node 24 action"
Assert-True ($rootContent -match 'RAG_DEMO_ADMIN_PASSWORD\s+=\s+var\.demo_admin_password_secret_arn') "migration must receive the deployed admin password from Secrets Manager"
Assert-True (($iamContent -split 'secretsmanager:GetSecretValue').Count -eq 3) "only the ECS execution role and deploy role may read their scoped Secrets Manager values"
Assert-True ($iamContent -match 'sid\s+=\s+"ReadDeploymentConfig"') "deploy role access must be limited to the deployment config secret"
Assert-True ($iamContent -match 'local\.bedrock_rerank_model_arn') "rerank model ARN must be included in bedrock InvokeModel resources"
Assert-True ($iamContent -match 'actions\s+=\s+\["s3:ListBucket"\]') "missing-object checks need bucket-level ListBucket"
Assert-True ($iamContent -match 'values\s+=\s+\["\$\{var\.documents_key_prefix\}/\*"\]') "ListBucket must be constrained to the documents prefix"
Assert-True ($rootContent -match 'GENERATION_MAX_OUTPUT_TOKENS\s+=\s+"5000"') "Nova Lite generation must stay within its 5K output limit"
Assert-True ($planWorkflow -match "github\.ref == 'refs/heads/deploy/AWS_ECS'") "manual Terraform plan must be restricted to deploy/AWS_ECS"

Write-Host "aws-demo parser and credential-free tests passed."
