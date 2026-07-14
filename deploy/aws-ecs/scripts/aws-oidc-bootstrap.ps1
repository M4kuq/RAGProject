[CmdletBinding()]
param(
  [ValidateSet("plan", "apply")]
  [string]$Command = "plan",
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
  [string]$Repository,
  [ValidatePattern("^[A-Za-z0-9._/-]+$")]
  [string]$Branch = "deploy/AWS_ECS",
  [ValidatePattern("^[A-Za-z0-9_+=,.@-]+$")]
  [string]$RoleName = "ragproject-demo-github-oidc-smoke",
  [string]$Profile = "ragproject-aws",
  [string]$Confirmation = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:OidcBootstrapProviderUrl = "https://token.actions.githubusercontent.com"
$script:OidcBootstrapProviderHost = "token.actions.githubusercontent.com"
$script:OidcBootstrapAudience = "sts.amazonaws.com"
$script:OidcBootstrapConfirmation = "CREATE-GITHUB-OIDC-SMOKE"

function Assert-OidcBootstrapCommandAvailable {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command is unavailable: $Name"
  }
}

function Test-OidcBootstrapAccountAllowed {
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

function Assert-OidcBootstrapConfirmation {
  param([Parameter(Mandatory = $true)][string]$Value)
  if ($Value -cne $script:OidcBootstrapConfirmation) {
    throw "apply requires -Confirmation $script:OidcBootstrapConfirmation."
  }
}

function Invoke-OidcBootstrapAwsJson {
  param(
    [Parameter(Mandatory = $true)][string[]]$ArgumentList,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [switch]$AllowNotFound
  )
  $arguments = $ArgumentList + @(
    "--profile", $AwsProfile,
    "--output", "json",
    "--no-cli-pager"
  )
  $output = & aws @arguments 2>&1
  $exitCode = $LASTEXITCODE
  $text = (($output | Out-String).Trim())
  if ($exitCode -ne 0) {
    if ($AllowNotFound -and $text -match "NoSuchEntity") { return $null }
    throw "AWS CLI request failed with exit code $exitCode; output is suppressed to protect identifiers."
  }
  if ([string]::IsNullOrWhiteSpace($text)) { return $null }
  try {
    return $text | ConvertFrom-Json
  } catch {
    throw "AWS CLI returned invalid JSON; output is suppressed to protect identifiers."
  }
}

function New-OidcBootstrapTrustPolicy {
  param(
    [Parameter(Mandatory = $true)][string]$ProviderArn,
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$DeployBranch
  )
  $subject = "repo:${Repo}:ref:refs/heads/${DeployBranch}"
  return [ordered]@{
    Version = "2012-10-17"
    Statement = @(
      [ordered]@{
        Effect = "Allow"
        Principal = [ordered]@{ Federated = $ProviderArn }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = [ordered]@{
          StringEquals = [ordered]@{
            "token.actions.githubusercontent.com:aud" = $script:OidcBootstrapAudience
            "token.actions.githubusercontent.com:sub" = $subject
          }
        }
      }
    )
  }
}

function ConvertFrom-OidcBootstrapPolicyDocument {
  param([Parameter(Mandatory = $true)]$Document)
  if ($Document -is [string]) {
    try {
      return ([Uri]::UnescapeDataString($Document) | ConvertFrom-Json)
    } catch {
      throw "The existing role trust policy is invalid; content is suppressed to protect identifiers."
    }
  }
  return $Document
}

function Get-OidcBootstrapPolicyValue {
  param(
    [Parameter(Mandatory = $true)]$Container,
    [Parameter(Mandatory = $true)][string]$Name
  )
  if ($Container -is [System.Collections.IDictionary]) {
    if (-not $Container.Contains($Name)) { return $null }
    return $Container[$Name]
  }
  $property = $Container.PSObject.Properties[$Name]
  if ($null -eq $property) { return $null }
  return $property.Value
}

function Get-OidcBootstrapPolicyKeyCount {
  param([Parameter(Mandatory = $true)]$Container)
  if ($Container -is [System.Collections.IDictionary]) {
    return $Container.Count
  }
  return @($Container.PSObject.Properties).Count
}

function Test-OidcBootstrapTrustPolicy {
  param(
    [Parameter(Mandatory = $true)]$Policy,
    [Parameter(Mandatory = $true)][string]$ProviderArn,
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$DeployBranch
  )
  $document = ConvertFrom-OidcBootstrapPolicyDocument $Policy
  if ([string]$document.Version -cne "2012-10-17") { return $false }
  $statements = @($document.Statement)
  if ($statements.Count -ne 1) { return $false }
  $statement = $statements[0]
  if ((Get-OidcBootstrapPolicyKeyCount $statement) -ne 4) { return $false }
  if ([string]$statement.Effect -cne "Allow") { return $false }
  if (@($statement.Action).Count -ne 1 -or [string]$statement.Action -cne "sts:AssumeRoleWithWebIdentity") {
    return $false
  }
  if (@($statement.Principal.Federated).Count -ne 1 -or [string]$statement.Principal.Federated -cne $ProviderArn) {
    return $false
  }
  if ((Get-OidcBootstrapPolicyKeyCount $statement.Principal) -ne 1) { return $false }
  if ((Get-OidcBootstrapPolicyKeyCount $statement.Condition) -ne 1) { return $false }
  $stringEquals = $statement.Condition.StringEquals
  if ($null -eq $stringEquals) { return $false }
  if ((Get-OidcBootstrapPolicyKeyCount $stringEquals) -ne 2) { return $false }
  $audience = Get-OidcBootstrapPolicyValue $stringEquals "token.actions.githubusercontent.com:aud"
  $subject = Get-OidcBootstrapPolicyValue $stringEquals "token.actions.githubusercontent.com:sub"
  if ($null -eq $audience -or $null -eq $subject) { return $false }
  $expectedSubject = "repo:${Repo}:ref:refs/heads/${DeployBranch}"
  return (
    [string]$audience -ceq $script:OidcBootstrapAudience -and
    [string]$subject -ceq $expectedSubject
  )
}

function Invoke-OidcBootstrap {
  Assert-OidcBootstrapCommandAvailable "aws"
  $allowlist = [Environment]::GetEnvironmentVariable("AWS_DEMO_ALLOWED_ACCOUNT_IDS")
  if ([string]::IsNullOrWhiteSpace($allowlist)) {
    throw "AWS_DEMO_ALLOWED_ACCOUNT_IDS is required."
  }
  if ($Command -eq "apply") {
    Assert-OidcBootstrapConfirmation $Confirmation
  }

  $identity = Invoke-OidcBootstrapAwsJson @(
    "sts", "get-caller-identity"
  ) $Profile
  $accountId = [string]$identity.Account
  if (-not (Test-OidcBootstrapAccountAllowed $accountId $allowlist)) {
    throw "The active AWS account is not in AWS_DEMO_ALLOWED_ACCOUNT_IDS."
  }

  $providerArn = "arn:aws:iam::${accountId}:oidc-provider/$script:OidcBootstrapProviderHost"
  $providerList = Invoke-OidcBootstrapAwsJson @(
    "iam", "list-open-id-connect-providers"
  ) $Profile
  $providerExists = @(
    @($providerList.OpenIDConnectProviderList) |
      Where-Object { [string]$_.Arn -ceq $providerArn }
  ).Count -eq 1
  if ($providerExists) {
    $provider = Invoke-OidcBootstrapAwsJson @(
      "iam", "get-open-id-connect-provider", "--open-id-connect-provider-arn", $providerArn
    ) $Profile
    if (@($provider.ClientIDList) -notcontains $script:OidcBootstrapAudience) {
      throw "The existing GitHub OIDC provider does not include the required audience; refusing to change it."
    }
  }

  $roleResult = Invoke-OidcBootstrapAwsJson @(
    "iam", "get-role", "--role-name", $RoleName
  ) $Profile -AllowNotFound
  $roleExists = $null -ne $roleResult
  if ($roleExists -and -not (Test-OidcBootstrapTrustPolicy $roleResult.Role.AssumeRolePolicyDocument $providerArn $Repository $Branch)) {
    throw "The existing smoke role trust policy differs from the expected exact repository/branch trust; refusing to change it."
  }

  $providerAction = if ($providerExists) { "reuse" } else { "create" }
  $roleAction = if ($roleExists) { "reuse" } else { "create" }
  Write-Host "GitHub OIDC smoke bootstrap review:"
  Write-Host "  provider: $providerAction"
  Write-Host "  role: $roleAction"
  Write-Host "  repository: $Repository"
  Write-Host "  branch: $Branch"
  Write-Host "  attached permission policies: none"

  if ($Command -eq "plan") {
    Write-Host "No AWS resources were changed."
    return
  }

  if (-not $providerExists) {
    Invoke-OidcBootstrapAwsJson @(
      "iam", "create-open-id-connect-provider",
      "--url", $script:OidcBootstrapProviderUrl,
      "--client-id-list", $script:OidcBootstrapAudience
    ) $Profile | Out-Null
  }
  if (-not $roleExists) {
    $trustPolicy = New-OidcBootstrapTrustPolicy $providerArn $Repository $Branch
    $trustPolicyJson = $trustPolicy | ConvertTo-Json -Depth 10 -Compress
    Invoke-OidcBootstrapAwsJson @(
      "iam", "create-role",
      "--role-name", $RoleName,
      "--description", "GitHub Actions OIDC smoke role without attached permissions",
      "--assume-role-policy-document", $trustPolicyJson
    ) $Profile | Out-Null
  }
  Write-Host "GitHub OIDC provider and permissionless smoke role are ready."
  Write-Host "Set AWS_OIDC_SMOKE_ROLE_ARN as a GitHub repository variable without printing it to logs."
}

if ($MyInvocation.InvocationName -ne ".") {
  Invoke-OidcBootstrap
}
