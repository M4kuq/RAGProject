# AWS IAM OIDC trust cutover runbook

## 目的と適用範囲

このrunbookは、Phase4 D1a（[RAG-18](https://keichannel116.atlassian.net/browse/RAG-18)）として、GitHub ActionsのOIDC trustを旧`deploy/AWS_ECS` branchから`main`へ切り替えるための人間向け手順である。

対象は次の3系統、合計4 roleに限る。

1. bootstrap stackのTerraform plan roleとTerraform lifecycle role
2. root stackのGitHub deploy role
3. permissionless OIDC smoke role

このrunbookの作成時にはAWS API、Terraform plan/apply、GitHub Actions workflow、GitHub設定変更を実行していない。以下のコマンドは、権限を持つ人間が保守時間帯に上から順に実行するためのものである。D1aではruntime resourceの作成・削除、`.tf` / `.ps1`の変更、旧branch削除、ephemeral lifecycleの実測を行わない。

## 1. 2026-07-27時点の静的な現状記録

基準はPR #126を含む`origin/main`のcommit `5f16ba5`である。

| 系統 | repository内の現在値 | 影響するrole | 参照元 |
|---|---|---|---|
| bootstrap | `github_deploy_branch` defaultは`main` | Terraform plan role、Terraform lifecycle role | `deploy/aws-ecs/bootstrap/variables.tf:48-56` |
| root | `github_deploy_branch` defaultは`main` | GitHub deploy role | `deploy/aws-ecs/variables.tf:233-237` |
| OIDC smoke | `Branch` defaultは`main` | permissionless OIDC smoke role | `deploy/aws-ecs/scripts/aws-oidc-bootstrap.ps1:1-13` |

root deploy roleのtrust subjectは`deploy/aws-ecs/modules/iam/main.tf:63-66`で次の形に組み立てられる。

```hcl
values = ["repo:${var.github_oidc_repo}:ref:refs/heads/${var.github_deploy_branch}"]
```

bootstrapの2 roleも同じsubject形を`deploy/aws-ecs/bootstrap/access.tf:32-35`と`deploy/aws-ecs/bootstrap/lifecycle.tf:65-68`で使う。OIDC smoke scriptは`deploy/aws-ecs/scripts/aws-oidc-bootstrap.ps1:87-107`で同じ形のpolicyを生成し、`146-178`で完全一致を検証する。

GitHub repository variable `DEPLOY_BRANCH`は、2026-07-27のread-only確認では`deploy/AWS_ECS`であった。この値は`AWS Infra Plan`の`TF_VAR_github_deploy_branch`に渡る（`.github/workflows/aws-infra-plan.yml:69-75`）。一方、`AWS Demo Lifecycle`は既に`main`を明示している（`.github/workflows/aws-demo.yml:48-54`）。

AWS上の実trustは、このrunbook作成時には参照していない。D0で記録された「4 roleのtrustは旧branchを許可したまま」を開始仮説とし、手順1のlocal preflightで必ず再確認する。仮説どおりなら、`main` workflowはbranch guardを通過した後、`aws-actions/configure-aws-credentials`による`AssumeRoleWithWebIdentity`で拒否され、Terraformやsmoke本体へ進まない。

### 追加のbranch-scoped roleの調査結果

`sts:AssumeRoleWithWebIdentity`、GitHub OIDC subject、`github_deploy_branch`のrepository内検索では、上記3系統以外のbranch-scoped role定義は見つからなかった。workflowが参照するroleとの対応は次のとおりである。

| workflow | role系統 |
|---|---|
| `AWS Infra Plan` | bootstrap Terraform plan role |
| `AWS Demo Lifecycle` | bootstrap Terraform lifecycle role |
| `AWS Deploy App` / `AWS Deploy Frontend` | root GitHub deploy role |
| `AWS OIDC Smoke` | permissionless OIDC smoke role |

## 2. `aws-demo.ps1`経由で切り替えられない理由

`deploy/aws-ecs/scripts/aws-demo.ps1:138-139`はTerraform実行前に、Terraformへ渡すbranchとscriptの期待branchが一致することを強制する。

```powershell
if ($env:TF_VAR_github_deploy_branch -ne $script:ExpectedBranch) {
  throw "TF_VAR_github_deploy_branch must be $script:ExpectedBranch."
}
```

旧branch側のscriptでは`ExpectedBranch`が旧branchのままである。そのため、旧branchから`TF_VAR_github_deploy_branch=main`を渡すと必ずthrowし、Terraformへ到達しない。D1aは`aws-demo.ps1`を変更せず、人間がlocal認証でTerraformを直接実行する。guardを一時的に外す方法はfail-closedを弱め、復元漏れも起こり得るため推奨しない。

## 3. RAG-17 apply前確認

### 3.1 state分離とbackend値

| stack | backend | 現状 | D1aでの扱い |
|---|---|---|---|
| bootstrap | backend blockがないためimplicit local backend | 権威あるstateは、そのbootstrap作業ディレクトリの`terraform.tfstate` | stateを読んだりcommitしたりせず、存在確認と安全なlocal backupを行う |
| root | S3 remote backend | `backend.tf:7,10`のbucket / lock tableはplaceholder。`key`と`region`はbootstrap defaultと一致 | bootstrapの`backend_config` outputを変数へ取り込み、`terraform init -reconfigure -backend-config=...`で全項目を上書きする |

bootstrapの`backend_config` outputは`deploy/aws-ecs/bootstrap/outputs.tf:11-19`にあり、bucket、lock table、region、key、encryptを一組で返す。`DEPLOY.md:146-170`と`README.md:71-100`も「bootstrapをlocal backendで先にapplyし、そのoutputsをroot backendへ渡す」という同じ順序を記載している。したがって移行方針は現行docsと一致する。

`backend.tf`にあるkey / regionは現行bootstrap defaultと一致しており、静的な不整合はない。ただし、過去のbootstrap applyで`state_key`または`region`を上書きしていた場合、固定値を使うと誤ったstateへ接続する。必ず`backend_config` outputの全項目を渡し、placeholderを使ったままinitしない。

### 3.2 apply入力とoutputs

bootstrap変数にはすべてdefaultがある。ただし、過去のapplyでtfvarsを使った場合は同じtfvarsを再利用し、`github_deploy_branch`だけをCLIで`main`へ上書きする。特に`project`、`environment`、`github_oidc_repo`、`github_oidc_provider_arn`を過去と変えてはならない。

root stackにはdefaultのない変数とsensitiveな入力がある。新しい値をD1aで組み立てず、直近の正常なroot applyで使用した同一のtfvarsまたは同等の安全な環境注入を再利用する。branchだけをCLIで`main`へ上書きする。

利用できるoutputsは次のとおりである。

| output | 用途 |
|---|---|
| bootstrap `backend_config` | root remote backendの完全なinit設定 |
| bootstrap `terraform_plan_role_arn` | plan role名を画面へ出さずに取得 |
| bootstrap `terraform_lifecycle_role_arn` | lifecycle role名を画面へ出さずに取得 |
| bootstrap `github_oidc_provider_arn` | rootの既存OIDC provider入力 |
| root `github_deploy_role_arn` | deploy role名を画面へ出さずに取得 |

### 3.3 必要権限

local実行主体には、少なくとも次が必要である。

- bootstrap local stateが管理するresourceのreadと、bootstrap 2 roleのtrust更新
- root remote stateのread/writeとlock、root deploy roleのtrust更新
- smoke roleのreadとtrust更新
- caller accountが既存の`AWS_DEMO_ALLOWED_ACCOUNT_IDS`に含まれること

bootstrap lifecycle roleのpolicyにはroot runtime roleのtrust更新権限があるが、bootstrap自身の2 roleや別管理のsmoke roleは対象外である。したがって、4 roleすべてを復旧できる既存のlocal運用権限が必要であり、GitHub OIDCだけに依存してはならない。

### 3.4 未解決の前提

次はrepositoryの静的調査だけでは確定できない。いずれかを満たせない場合はapplyせず停止する。

- 権威あるbootstrap local stateを保持するディレクトリの実path
- bootstrapとrootで過去に使用したtfvarsの実pathと同一性
- local実行主体が上記権限を持つこと
- GitHub secretが現在の4 roleを指していること
- AWS上の各trustが本当に旧branchだけを許可しているか、または既に一部切り替わっているか

## 4. 実行順序と切り替え点

順序は次のとおりとする。

1. local account、state、入力、4 trustをpreflightし、trust policyをlocal一時領域へbackupする
2. bootstrapのplan / lifecycle roleを`main`へ更新する
3. rootのdeploy roleを`main`へ更新する
4. smoke roleを`main`へ更新する
5. repository variable `DEPLOY_BRANCH`を`main`へ更新する
6. `main`からOIDC smokeだけを実行する
7. 4 roleとrepository設定から旧branch依存が消えたことを確認する
8. rollback不要と判断した後、repository外の一時artifactを安全に削除する

role trustを先に揃え、repository variableとworkflow実行を最後にすることで、「新設定を公開したが対応roleがまだassumeできない」時間を最小化する。`DEPLOY_BRANCH`を先に変更してもAWS trustは変わらず、失敗するworkflowを増やすだけなので先行させない。

| 完了時点 | `main`からAssumeRole可能になる対象 |
|---|---|
| 手順2のapply直後 | Terraform plan role、Terraform lifecycle role |
| 手順3のapply直後 | GitHub deploy role |
| 手順4のIAM更新直後 | OIDC smoke role |
| 手順5のvariable更新直後 | 3系統とworkflow入力が整合した状態 |

D1aで実行確認するworkflowはpermissionlessな`AWS OIDC Smoke`だけとする。Terraform plan、lifecycle、app/frontend deployはD1aでは実行しない。

## 5. 実行手順

すべて同じPowerShell sessionで実行する。実値をterminalへ表示、clipboardへcopy、issue / PR / chatへ貼付してはならない。plan、state、trust backupはaccount識別子やpolicyを含む可能性があるため、repository外の一時ディレクトリだけに置く。

開始時の`origin/main` commitを`$MainSha`へ記録し、cutover中の基準として固定する。途中で`main`が動くと、local Terraform planとoperational-file検査が古いcommitのままなのに、workflow dispatchだけが新しいcommitを実行し、実際にtrustを付与した設定を検証しないため危険である。

### 手順1: local preflight、state入力、trust backup

**cwd:** 最初は任意。`$MainRepoRoot`と`$BootstrapDir`を絶対pathで設定した後、command自身がcwdを切り替える。

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$MainRepoRoot = "<ABSOLUTE_PATH_TO_CLEAN_MAIN_WORKTREE>"
$BootstrapDir = "<ABSOLUTE_PATH_TO_AUTHORITATIVE_BOOTSTRAP_DIR>"
$RootTfvars = "<ABSOLUTE_PATH_TO_EXISTING_ROOT_TFVARS>"
$BootstrapTfvars = "" # 過去に使った場合だけ絶対path。未使用なら空文字
$AwsProfile = "<AWS_PROFILE>"
$Repository = "<OWNER>/<REPO>"
$SmokeRoleName = "ragproject-demo-github-oidc-smoke"
$OldBranch = "deploy/AWS_ECS"
$NewBranch = "main"

$RequiredText = @($MainRepoRoot, $BootstrapDir, $RootTfvars, $AwsProfile, $Repository)
if (@($RequiredText | Where-Object { $_ -match "^<.*>$" -or [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
  throw "Replace every required placeholder before continuing."
}
if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
  throw "Repository must use OWNER/REPO format."
}

$MainRepoRoot = (Resolve-Path -LiteralPath $MainRepoRoot).Path
$BootstrapDir = (Resolve-Path -LiteralPath $BootstrapDir).Path
$RootTfvars = (Resolve-Path -LiteralPath $RootTfvars).Path
if ($BootstrapTfvars) {
  $BootstrapTfvars = (Resolve-Path -LiteralPath $BootstrapTfvars).Path
}

foreach ($CommandName in @("git", "terraform", "aws", "gh")) {
  if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
    throw "Required command is unavailable: $CommandName"
  }
}

function Assert-NoUntrackedTerraformConfiguration {
  param(
    [Parameter(Mandatory = $true)][string]$TerraformDirectory,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $UnignoredPaths = @(
    git -C $TerraformDirectory ls-files --others --exclude-standard -- .
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect untracked files: $Label"
  }
  $IgnoredPaths = @(
    git -C $TerraformDirectory ls-files --others --ignored --exclude-standard -- .
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect ignored files: $Label"
  }
  $TerraformConfigNamePattern = (
    "(?i)^(?:override\.tf(?:\.json)?|.+_override\.tf(?:\.json)?|.+\.tf(?:\.json)?)$"
  )
  $UntrackedTerraformConfiguration = @(
    @($UnignoredPaths + $IgnoredPaths) |
      Sort-Object -Unique |
      Where-Object {
        $_ -notmatch "(^|/)\.terraform(/|$)" -and
        (Split-Path -Leaf $_) -match $TerraformConfigNamePattern
      }
  )
  if ($UntrackedTerraformConfiguration.Count -ne 0) {
    throw "Untracked Terraform configuration exists (including override files): $Label"
  }
}

Set-Location -LiteralPath $MainRepoRoot
if ((git branch --show-current).Trim() -cne "main") {
  throw "MainRepoRoot must be on main."
}
git fetch origin --prune
if ($LASTEXITCODE -ne 0) {
  throw "Could not refresh origin refs."
}
$MainSha = (git rev-parse origin/main).Trim()
if ($LASTEXITCODE -ne 0 -or $MainSha -notmatch "^[0-9a-f]{40,64}$") {
  throw "Could not record the origin/main commit."
}
git merge-base --is-ancestor 5f16ba5 HEAD
if ($LASTEXITCODE -ne 0) {
  throw "PR #126 commit is not an ancestor of HEAD."
}
if ((git rev-parse HEAD).Trim() -cne $MainSha) {
  throw "HEAD must equal the already-fetched origin/main."
}
if (@(git status --porcelain --untracked-files=no).Count -ne 0) {
  throw "MainRepoRoot has tracked changes."
}
Assert-NoUntrackedTerraformConfiguration `
  (Join-Path $MainRepoRoot "deploy/aws-ecs") `
  "Main root stack"

function Assert-MainShaUnchanged {
  param([Parameter(Mandatory = $true)][string]$Checkpoint)
  Set-Location -LiteralPath $MainRepoRoot
  git fetch origin --prune
  if ($LASTEXITCODE -ne 0) {
    throw "Could not refresh origin refs at checkpoint: $Checkpoint"
  }
  $ObservedMainSha = (git rev-parse origin/main).Trim()
  $ObservedHeadSha = (git rev-parse HEAD).Trim()
  if ($ObservedMainSha -cne $MainSha -or $ObservedHeadSha -cne $MainSha) {
    throw "The recorded main commit changed at checkpoint: $Checkpoint. Stop before the next update or roll back completed updates."
  }
  if (@(git status --porcelain --untracked-files=no).Count -ne 0) {
    throw "MainRepoRoot gained tracked changes at checkpoint: $Checkpoint"
  }
  Assert-NoUntrackedTerraformConfiguration `
    (Join-Path $MainRepoRoot "deploy/aws-ecs") `
    "Main root stack at checkpoint: $Checkpoint"
}

$GhRepositoryJson = (gh repo view --json nameWithOwner | Out-String)
if ($LASTEXITCODE -ne 0) {
  throw "Could not resolve the GitHub repository."
}
$GhRepository = ConvertFrom-Json -InputObject $GhRepositoryJson
if ([string]$GhRepository.nameWithOwner -cne $Repository) {
  throw "gh is targeting a different repository."
}

$BootstrapState = Join-Path $BootstrapDir "terraform.tfstate"
if (-not (Test-Path -LiteralPath $BootstrapState -PathType Leaf)) {
  throw "Authoritative bootstrap local state was not found. Do not apply from empty state."
}
$BootstrapRepoRoot = (git -C $BootstrapDir rev-parse --show-toplevel).Trim()
git -C $BootstrapRepoRoot diff --quiet HEAD -- deploy/aws-ecs/bootstrap
if ($LASTEXITCODE -ne 0) {
  throw "Bootstrap configuration has uncommitted changes."
}
git -C $BootstrapRepoRoot diff --cached --quiet HEAD -- deploy/aws-ecs/bootstrap
if ($LASTEXITCODE -ne 0) {
  throw "Bootstrap configuration has staged changes."
}
Assert-NoUntrackedTerraformConfiguration $BootstrapDir "BootstrapDir"
git -C $BootstrapRepoRoot diff --quiet origin/main -- deploy/aws-ecs/bootstrap ":!deploy/aws-ecs/bootstrap/variables.tf"
if ($LASTEXITCODE -ne 0) {
  throw "Bootstrap configuration differs from origin/main outside variables.tf."
}
$VariablesDiff = @(
  git -C $BootstrapRepoRoot diff --unified=0 origin/main -- deploy/aws-ecs/bootstrap/variables.tf
)
$VariableChangeLines = @(
  $VariablesDiff |
    Where-Object { $_ -match "^[+-]" -and $_ -notmatch "^[+-]{3}" }
)
$ExpectedVariableChangeLines = @(
  '-  default     = "deploy/AWS_ECS"',
  '+  default     = "main"'
)
$UnexpectedVariableChangeLines = @(
  Compare-Object $ExpectedVariableChangeLines $VariableChangeLines
)
if (
  $VariableChangeLines.Count -ne 0 -and
  ($VariableChangeLines.Count -ne 2 -or $UnexpectedVariableChangeLines.Count -ne 0)
) {
  throw "Bootstrap variables.tf differs from origin/main in an unexpected way."
}

if ([string]::IsNullOrWhiteSpace($env:AWS_DEMO_ALLOWED_ACCOUNT_IDS)) {
  throw "AWS_DEMO_ALLOWED_ACCOUNT_IDS must already be present in the operator environment."
}
$ProfileCallerAccount = (
  aws sts get-caller-identity --profile $AwsProfile --query Account --output text --no-cli-pager
).Trim()
if ($LASTEXITCODE -ne 0 -or $ProfileCallerAccount -notmatch "^[0-9]{12}$") {
  throw "Could not validate the explicitly selected profile caller account."
}
$AllowedAccounts = @(
  $env:AWS_DEMO_ALLOWED_ACCOUNT_IDS.Split(",") |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match "^[0-9]{12}$" }
)
if ($AllowedAccounts -notcontains $ProfileCallerAccount) {
  throw "The explicitly selected profile caller account is not allowlisted."
}

$OriginalAwsProfileWasSet = Test-Path Env:AWS_PROFILE
$OriginalAwsProfile = if ($OriginalAwsProfileWasSet) {
  [string]$env:AWS_PROFILE
} else {
  $null
}

function Restore-OriginalAwsProfile {
  if ($OriginalAwsProfileWasSet) {
    $env:AWS_PROFILE = $OriginalAwsProfile
  } else {
    Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
  }
}

$env:AWS_PROFILE = $AwsProfile
try {
  $TerraformEnvironmentCallerAccount = (
    aws sts get-caller-identity --query Account --output text --no-cli-pager
  ).Trim()
  if (
    $LASTEXITCODE -ne 0 -or
    $TerraformEnvironmentCallerAccount -notmatch "^[0-9]{12}$" -or
    $AllowedAccounts -notcontains $TerraformEnvironmentCallerAccount -or
    $TerraformEnvironmentCallerAccount -cne $ProfileCallerAccount
  ) {
    throw "The Terraform process environment does not resolve to the allowlisted selected profile account."
  }
} catch {
  Restore-OriginalAwsProfile
  throw
}

function Invoke-Terraform {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [AllowEmptyCollection()]
    [string[]]$TerraformArguments
  )
  if ([string]$env:AWS_PROFILE -cne $AwsProfile) {
    throw "AWS_PROFILE changed after preflight; refusing to run Terraform."
  }
  & terraform @TerraformArguments
}

Write-Host "Selected profile and Terraform environment caller allowlist: OK"

$IsWindowsPlatform = [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
  [Runtime.InteropServices.OSPlatform]::Windows
)
if ($IsWindowsPlatform) {
  if (-not (Get-Command "icacls.exe" -ErrorAction SilentlyContinue)) {
    throw "icacls.exe is required to protect cutover artifacts on Windows."
  }
  $CurrentWindowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
} elseif (-not (Get-Command "chmod" -ErrorAction SilentlyContinue)) {
  throw "chmod is required to protect cutover artifacts on Unix."
}

function Protect-CutoverPath {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][ValidateSet("Directory", "File")][string]$Kind
  )
  if ($IsWindowsPlatform) {
    $AclGrant = if ($Kind -ceq "Directory") {
      "${CurrentWindowsIdentity}:(OI)(CI)F"
    } else {
      "${CurrentWindowsIdentity}:F"
    }
    & icacls.exe $Path /inheritance:r /grant:r $AclGrant | Out-Null
  } else {
    $Mode = if ($Kind -ceq "Directory") { "700" } else { "600" }
    & chmod $Mode -- $Path
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict cutover artifact access: $Kind"
  }
}

function Protect-CutoverFileIfPresent {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    Protect-CutoverPath $Path "File"
  }
}

$CutoverDir = Join-Path ([IO.Path]::GetTempPath()) (
  "ragproject-trust-cutover-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
)
New-Item -ItemType Directory -Path $CutoverDir -ErrorAction Stop | Out-Null
Protect-CutoverPath $CutoverDir "Directory"
$BootstrapStateBackup = Join-Path $CutoverDir "bootstrap.terraform.tfstate.before"
Copy-Item -LiteralPath $BootstrapState -Destination $BootstrapStateBackup -ErrorAction Stop
Protect-CutoverPath $BootstrapStateBackup "File"

$BootstrapVarArgs = @()
if ($BootstrapTfvars) {
  $BootstrapVarArgs += "-var-file=$BootstrapTfvars"
}
Invoke-Terraform "-chdir=$BootstrapDir" init -input=false
if ($LASTEXITCODE -ne 0) {
  throw "Bootstrap terraform init failed. No apply was attempted."
}

$BackendJson = (
  Invoke-Terraform "-chdir=$BootstrapDir" output -json backend_config | Out-String
)
if ($LASTEXITCODE -ne 0) {
  throw "Could not read backend_config from authoritative bootstrap state."
}
$Backend = ConvertFrom-Json -InputObject $BackendJson
foreach ($Name in @("bucket", "key", "region", "dynamodb_table")) {
  $BackendProperty = $Backend.PSObject.Properties[$Name]
  if (
    $null -eq $BackendProperty -or
    [string]::IsNullOrWhiteSpace([string]$BackendProperty.Value) -or
    [string]$BackendProperty.Value -match "REPLACE"
  ) {
    throw "backend_config contains a missing or placeholder value: $Name"
  }
}

Invoke-Terraform "-chdir=$(Join-Path $MainRepoRoot 'deploy/aws-ecs')" init -input=false -reconfigure `
  "-backend-config=bucket=$($Backend.bucket)" `
  "-backend-config=key=$($Backend.key)" `
  "-backend-config=region=$($Backend.region)" `
  "-backend-config=dynamodb_table=$($Backend.dynamodb_table)" `
  "-backend-config=encrypt=true"
if ($LASTEXITCODE -ne 0) {
  throw "Root backend init failed. Confirm bootstrap outputs; do not edit backend.tf with guessed values."
}

function Get-RoleNameFromOutput {
  param(
    [Parameter(Mandatory = $true)][string]$TerraformDirectory,
    [Parameter(Mandatory = $true)][string]$OutputName
  )
  $RoleArn = (
    Invoke-Terraform "-chdir=$TerraformDirectory" output -raw $OutputName
  ).Trim()
  if ($LASTEXITCODE -ne 0 -or $RoleArn -notmatch "/([^/]+)$") {
    throw "Could not derive a role name from Terraform output: $OutputName"
  }
  $RoleName = $Matches[1]
  Remove-Variable RoleArn
  return $RoleName
}

$RootDir = Join-Path $MainRepoRoot "deploy/aws-ecs"
$PlanRoleName = Get-RoleNameFromOutput $BootstrapDir "terraform_plan_role_arn"
$LifecycleRoleName = Get-RoleNameFromOutput $BootstrapDir "terraform_lifecycle_role_arn"
$DeployRoleName = Get-RoleNameFromOutput $RootDir "github_deploy_role_arn"

$OidcAudienceKey = "token.actions.githubusercontent.com:aud"
$OidcSubjectKey = "token.actions.githubusercontent.com:sub"

function Get-OidcConditionValues {
  param(
    [AllowNull()][object]$Condition,
    [Parameter(Mandatory = $true)][string]$Operator,
    [Parameter(Mandatory = $true)][string]$ConditionKey
  )
  if ($null -eq $Condition) {
    return
  }
  $OperatorProperty = $Condition.PSObject.Properties[$Operator]
  if ($null -eq $OperatorProperty -or $null -eq $OperatorProperty.Value) {
    return
  }
  $ConditionProperty = $OperatorProperty.Value.PSObject.Properties[$ConditionKey]
  if ($null -ne $ConditionProperty) {
    $ConditionProperty.Value
  }
}

function Get-RequiredOidcSubjectProperty {
  param(
    [AllowNull()][object]$Condition,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $SubjectProperties = @(
    foreach ($Operator in @("StringEquals", "StringLike")) {
      if ($null -eq $Condition) {
        continue
      }
      $OperatorProperty = $Condition.PSObject.Properties[$Operator]
      if ($null -eq $OperatorProperty -or $null -eq $OperatorProperty.Value) {
        continue
      }
      $SubjectProperty = $OperatorProperty.Value.PSObject.Properties[$OidcSubjectKey]
      if ($null -ne $SubjectProperty) {
        $SubjectProperty
      }
    }
  )
  if ($SubjectProperties.Count -ne 1) {
    throw "Expected exactly one StringEquals/StringLike OIDC subject: $Label"
  }
  return $SubjectProperties[0]
}

function ConvertTo-NormalizedJsonValue {
  param([AllowNull()][object]$Value)
  if ($null -eq $Value) {
    return $null
  }
  if ($Value -is [array]) {
    $NormalizedItems = [Collections.Generic.List[object]]::new()
    foreach ($Item in $Value) {
      [void]$NormalizedItems.Add((ConvertTo-NormalizedJsonValue $Item))
    }
    Write-Output -NoEnumerate $NormalizedItems.ToArray()
    return
  }
  if ($Value -is [pscustomobject]) {
    $NormalizedObject = [ordered]@{}
    $PropertyNames = @(
      $Value.PSObject.Properties.Name | Sort-Object -CaseSensitive
    )
    foreach ($PropertyName in $PropertyNames) {
      $NormalizedObject[$PropertyName] = ConvertTo-NormalizedJsonValue (
        $Value.PSObject.Properties[$PropertyName].Value
      )
    }
    return $NormalizedObject
  }
  return $Value
}

function ConvertTo-NormalizedJson {
  param([AllowNull()][object]$Value)
  $NormalizedValue = ConvertTo-NormalizedJsonValue $Value
  return ConvertTo-Json -InputObject $NormalizedValue -Depth 30 -Compress
}

function Get-JsonDifferencePaths {
  param(
    [AllowNull()][object]$Expected,
    [AllowNull()][object]$Actual,
    [Parameter(Mandatory = $true)][string]$Path
  )
  $ExpectedIsArray = $Expected -is [array]
  $ActualIsArray = $Actual -is [array]
  if ($ExpectedIsArray -ne $ActualIsArray) {
    $Path
    return
  }
  if ($ExpectedIsArray) {
    if ($Expected.Count -ne $Actual.Count) {
      "$Path.Count"
    }
    $CommonCount = [Math]::Min($Expected.Count, $Actual.Count)
    for ($Index = 0; $Index -lt $CommonCount; $Index++) {
      Get-JsonDifferencePaths $Expected[$Index] $Actual[$Index] "$Path[$Index]"
    }
    return
  }

  $ExpectedIsObject = $Expected -is [pscustomobject]
  $ActualIsObject = $Actual -is [pscustomobject]
  if ($ExpectedIsObject -ne $ActualIsObject) {
    $Path
    return
  }
  if ($ExpectedIsObject) {
    $PropertyNames = @(
      @(
        $Expected.PSObject.Properties.Name
        $Actual.PSObject.Properties.Name
      ) | Sort-Object -CaseSensitive -Unique
    )
    foreach ($PropertyName in $PropertyNames) {
      $ExpectedProperty = $Expected.PSObject.Properties[$PropertyName]
      $ActualProperty = $Actual.PSObject.Properties[$PropertyName]
      $PropertyPath = "$Path.$PropertyName"
      if ($null -eq $ExpectedProperty -or $null -eq $ActualProperty) {
        $PropertyPath
        continue
      }
      Get-JsonDifferencePaths $ExpectedProperty.Value $ActualProperty.Value $PropertyPath
    }
    return
  }

  if ((ConvertTo-NormalizedJson $Expected) -cne (ConvertTo-NormalizedJson $Actual)) {
    $Path
  }
}

function Assert-TrustPolicySubjectOnlyChange {
  param(
    [Parameter(Mandatory = $true)][object]$BeforePolicy,
    [Parameter(Mandatory = $true)][object]$ActualPolicy,
    [Parameter(Mandatory = $true)][string]$ExpectedBranch,
    [Parameter(Mandatory = $true)][string]$Label
  )
  try {
    $ExpectedPolicy = ConvertFrom-Json -InputObject (
      ConvertTo-Json -InputObject $BeforePolicy -Depth 30 -Compress
    )
  } catch {
    throw "Could not copy the trust policy for comparison; policy content was suppressed."
  }
  $ExpectedStatements = @($ExpectedPolicy.Statement)
  if ($ExpectedStatements.Count -ne 1) {
    throw "Expected exactly one trust statement before subject replacement: $Label"
  }
  $ExpectedConditionProperty = $ExpectedStatements[0].PSObject.Properties["Condition"]
  if ($null -eq $ExpectedConditionProperty) {
    throw "Expected trust policy has no Condition: $Label"
  }
  $ExpectedSubjectProperty = Get-RequiredOidcSubjectProperty `
    $ExpectedConditionProperty.Value `
    $Label
  $ExpectedSubjectValues = @($ExpectedSubjectProperty.Value)
  if ($ExpectedSubjectValues.Count -ne 1) {
    throw "Expected exactly one OIDC subject value before replacement: $Label"
  }
  $ExpectedSubject = "repo:${Repository}:ref:refs/heads/${ExpectedBranch}"
  if ($ExpectedSubjectProperty.Value -is [array]) {
    $ExpectedSubjectProperty.Value = @($ExpectedSubject)
  } else {
    $ExpectedSubjectProperty.Value = $ExpectedSubject
  }

  $ExpectedJson = ConvertTo-NormalizedJson $ExpectedPolicy
  $ActualJson = ConvertTo-NormalizedJson $ActualPolicy
  if ($ExpectedJson -cne $ActualJson) {
    $DifferencePaths = @(
      Get-JsonDifferencePaths $ExpectedPolicy $ActualPolicy '$' |
        Select-Object -First 8
    )
    if ($DifferencePaths.Count -eq 0) {
      $DifferencePaths = @('$')
    }
    throw (
      "Trust policy is not an exact subject-only change at key(s): {0}. " +
      "Policy values were suppressed."
    ) -f ($DifferencePaths -join ", ")
  }
}

function Write-RoleTrustPolicyForBranch {
  param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$DestinationPath,
    [Parameter(Mandatory = $true)][string]$TargetBranch,
    [Parameter(Mandatory = $true)][string]$Label
  )
  if ($TargetBranch -notin @($OldBranch, $NewBranch)) {
    throw "Refusing to generate a trust policy for an unexpected branch: $Label"
  }
  try {
    $BeforePolicy = Get-Content -LiteralPath $BackupPath -Raw | ConvertFrom-Json
    $Policy = ConvertFrom-Json -InputObject (
      ConvertTo-Json -InputObject $BeforePolicy -Depth 30 -Compress
    )
  } catch {
    throw "Could not parse the trust backup; policy content was suppressed."
  }
  $Statements = @($Policy.Statement)
  if ($Statements.Count -ne 1) {
    throw "Unexpected trust statement count while generating policy: $Label"
  }
  $ConditionProperty = $Statements[0].PSObject.Properties["Condition"]
  if ($null -eq $ConditionProperty) {
    throw "Trust policy has no Condition while generating policy: $Label"
  }
  $Condition = $ConditionProperty.Value
  $Audience = @(
    Get-OidcConditionValues $Condition "StringEquals" $OidcAudienceKey
  )
  $SubjectProperty = Get-RequiredOidcSubjectProperty $Condition $Label
  $SubjectValues = @($SubjectProperty.Value)
  $ExpectedSubjects = @(
    "repo:${Repository}:ref:refs/heads/${OldBranch}",
    "repo:${Repository}:ref:refs/heads/${NewBranch}"
  )
  if (
    $Audience.Count -ne 1 -or
    [string]$Audience[0] -cne "sts.amazonaws.com" -or
    $SubjectValues.Count -ne 1 -or
    [string]$SubjectValues[0] -notin $ExpectedSubjects
  ) {
    throw "Unexpected OIDC trust shape while generating policy: $Label"
  }
  $ExpectedTargetSubject = "repo:${Repository}:ref:refs/heads/${TargetBranch}"
  if ($SubjectProperty.Value -is [array]) {
    $SubjectProperty.Value = @($ExpectedTargetSubject)
  } else {
    $SubjectProperty.Value = $ExpectedTargetSubject
  }
  Assert-TrustPolicySubjectOnlyChange `
    $BeforePolicy `
    $Policy `
    $TargetBranch `
    "generated direct trust policy"
  [IO.File]::WriteAllText(
    $DestinationPath,
    ($Policy | ConvertTo-Json -Depth 20 -Compress),
    [Text.UTF8Encoding]::new($false)
  )
  Protect-CutoverPath $DestinationPath "File"
}

function Backup-RoleTrustAndGetBranch {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$RoleName
  )
  $RoleJson = (
    aws iam get-role --profile $AwsProfile --role-name $RoleName --output json --no-cli-pager |
      Out-String
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not read role trust: $Label"
  }
  $Role = ConvertFrom-Json -InputObject $RoleJson
  $Policy = $Role.Role.AssumeRolePolicyDocument
  $Statements = @($Policy.Statement)
  if ($Statements.Count -ne 1) {
    throw "Unexpected trust statement count: $Label"
  }
  $Actions = @($Statements[0].Action)
  $Principals = @($Statements[0].Principal.Federated)
  if (
    [string]$Statements[0].Effect -cne "Allow" -or
    $Actions.Count -ne 1 -or
    [string]$Actions[0] -cne "sts:AssumeRoleWithWebIdentity" -or
    $Principals.Count -ne 1 -or
    [string]::IsNullOrWhiteSpace([string]$Principals[0])
  ) {
    throw "Unexpected OIDC trust principal or action: $Label"
  }
  $ConditionProperty = $Statements[0].PSObject.Properties["Condition"]
  if ($null -eq $ConditionProperty) {
    throw "OIDC trust has no Condition: $Label"
  }
  $Condition = $ConditionProperty.Value
  $Audience = @(
    Get-OidcConditionValues $Condition "StringEquals" $OidcAudienceKey
  )
  $SubjectProperty = Get-RequiredOidcSubjectProperty $Condition $Label
  $Subject = @($SubjectProperty.Value)
  if (
    $Audience.Count -ne 1 -or
    [string]$Audience[0] -cne "sts.amazonaws.com" -or
    $Subject.Count -ne 1
  ) {
    throw "Unexpected OIDC trust shape: $Label"
  }
  $ExpectedPrefix = "repo:${Repository}:ref:refs/heads/"
  if (-not ([string]$Subject[0]).StartsWith($ExpectedPrefix, [StringComparison]::Ordinal)) {
    throw "Unexpected repository in OIDC trust: $Label"
  }
  $Branch = ([string]$Subject[0]).Substring($ExpectedPrefix.Length)
  if ($Branch -notin @($OldBranch, $NewBranch)) {
    throw "Unexpected branch in OIDC trust: $Label"
  }
  $BackupPath = Join-Path $CutoverDir "$Label.before.json"
  [IO.File]::WriteAllText(
    $BackupPath,
    ($Policy | ConvertTo-Json -Depth 20 -Compress),
    [Text.UTF8Encoding]::new($false)
  )
  Protect-CutoverPath $BackupPath "File"
  return $Branch
}

$BeforeBranches = [ordered]@{
  terraform_plan = Backup-RoleTrustAndGetBranch "terraform-plan" $PlanRoleName
  terraform_lifecycle = Backup-RoleTrustAndGetBranch "terraform-lifecycle" $LifecycleRoleName
  github_deploy = Backup-RoleTrustAndGetBranch "github-deploy" $DeployRoleName
  oidc_smoke = Backup-RoleTrustAndGetBranch "oidc-smoke" $SmokeRoleName
}
$BeforeBranches.GetEnumerator() | ForEach-Object {
  Write-Host ("{0}: {1}" -f $_.Key, $_.Value)
}

function Get-DeployBranchVariable {
  $VariablesJson = (gh variable list --json name,value | Out-String)
  if ($LASTEXITCODE -ne 0) {
    throw "gh variable list failed; output was suppressed."
  }
  $Variables = ConvertFrom-Json -InputObject $VariablesJson
  $Matches = @($Variables | Where-Object { [string]$_.name -ceq "DEPLOY_BRANCH" })
  if ($Matches.Count -ne 1) {
    throw "Expected exactly one DEPLOY_BRANCH repository variable."
  }
  $Value = [string]$Matches[0].value
  if ($Value -notmatch "^[A-Za-z0-9._/-]+$") {
    throw "DEPLOY_BRANCH is not a branch-shaped value; value was suppressed."
  }
  return $Value
}

$CurrentDeployBranch = Get-DeployBranchVariable
Write-Host "DEPLOY_BRANCH: $CurrentDeployBranch"
if ($CurrentDeployBranch -notin @($OldBranch, $NewBranch)) {
  throw "DEPLOY_BRANCH has an unexpected value."
}
```

**期待される結果**

- account IDやARNは表示されず、allowlistが`OK`になる。
- 4 roleはrole名ではなく論理labelとbranchだけが表示される。
- D0の想定どおりなら4 labelが`deploy/AWS_ECS`、`DEPLOY_BRANCH`も`deploy/AWS_ECS`になる。
- 既に一部が`main`なら、その事実を記録し、以後のplanでno-opになることを許容する。
- 開始時の`origin/main` SHAが`$MainSha`に保持され、以後のcheckpointではSHA自体を表示せず一致だけを検証する。
- `$env:AWS_PROFILE`はこのPowerShell processと子processだけに設定される。明示profile、同じprocess環境のcredential chain、後続plan JSON内のprovider caller accountを同じallowlistと明示profile accountに照合する。
- main root stackと別worktreeの`$BootstrapDir`の双方に、ignored fileを含む未追跡`.tf` / `.tf.json` / `override.tf` / `override.tf.json` / `*_override.tf`がない。
- `$CutoverDir`に4 trust backup、bootstrap state backupが作られる。pathを作業記録へ残すが、中身は表示しない。
- Unixでは`$CutoverDir`が`0700`、配下fileが`0600`になる。Windowsでは継承を遮断したACLにより実行userだけがdirectoryとfileへアクセスできる。

**失敗時**

- ここではAWS resource変更はない。原因を解消するまで進まない。
- bootstrap stateがない場合、新しい空stateからapplyしてはいけない。権威あるlocal stateまたは安全なbackupを特定する。
- trustが旧branch / `main`以外、複数subject、別repository、複数statementならscope外である。policyを自動整形せず、RAG-18を停止して別レビューへ送る。
- root backend initが失敗した場合、placeholderを実値へ直接置換してcommitしない。bootstrap outputとlocal権限を確認する。
- 手順1で停止し、以後TerraformまたはAWS recoveryを実行しない場合は`Restore-OriginalAwsProfile`を呼び、開始時の`AWS_PROFILE`へ戻す。rollbackが必要な場合は復旧完了まで設定を維持し、rollback末尾で戻す。
- AWS変更後にPowerShell sessionが失われた場合、手順1全体を再実行して新しいbefore backupを作り、元の開始状態と取り違えてはならない。元の`$CutoverDir`を再指定し、変数とfunction定義だけを読み込み直してロールバックを優先する。元のbackup pathを特定できなければ追加更新を停止する。

### 手順2: bootstrap plan / lifecycle roleを更新

**cwd:** 任意。`terraform -chdir=$BootstrapDir`を使う。

`-target`は通常運用向けではないが、このcutoverではbootstrap stackの他resourceを変更しないための例外的なblast-radius制限として使う。saved planの対象が2 roleのin-place updateだけであることを確認してから、そのplan fileをapplyする。

`Assert-TrustOnlyPlanChanges`は、planの更新前policyをdeep copyし、OIDC subjectの値だけを1箇所置換した期待値を作る。更新後policyはJSON objectのキー順序と空白・改行だけを正規化して完全一致させる。scalarと1要素配列、配列順序は同一shapeのまま比較するため、`Effect`、`Action`、`Principal`（`Federated` providerを含む）、audience、subject以外の全condition、statement数、condition operatorの種類と構成は不変でなければならない。

```powershell
function Assert-TrustOnlyPlanChanges {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Changes,
    [Parameter(Mandatory = $true)][string[]]$AllowedAddresses,
    [Parameter(Mandatory = $true)][string]$ExpectedBranch
  )
  foreach ($ResourceChange in $Changes) {
    if (
      $ResourceChange.address -notin $AllowedAddresses -or
      (@($ResourceChange.change.actions) -join ",") -cne "update"
    ) {
      throw "Plan contains an unexpected resource address or action."
    }
    $AttributeNames = @(
      @($ResourceChange.change.before.PSObject.Properties.Name) +
      @($ResourceChange.change.after.PSObject.Properties.Name) |
        Sort-Object -Unique
    )
    $ChangedAttributes = @(
      foreach ($AttributeName in $AttributeNames) {
        $BeforeProperty = $ResourceChange.change.before.PSObject.Properties[$AttributeName]
        $AfterProperty = $ResourceChange.change.after.PSObject.Properties[$AttributeName]
        $BeforeValue = [ordered]@{
          Exists = $null -ne $BeforeProperty
          Value = if ($null -eq $BeforeProperty) { $null } else { $BeforeProperty.Value }
        } | ConvertTo-Json -Depth 30 -Compress
        $AfterValue = [ordered]@{
          Exists = $null -ne $AfterProperty
          Value = if ($null -eq $AfterProperty) { $null } else { $AfterProperty.Value }
        } | ConvertTo-Json -Depth 30 -Compress
        if ($BeforeValue -cne $AfterValue) {
          $AttributeName
        }
      }
    )
    if ($ChangedAttributes.Count -ne 1 -or $ChangedAttributes[0] -cne "assume_role_policy") {
      throw "Plan changes a role attribute other than assume_role_policy."
    }
    $BeforePolicyProperty = $ResourceChange.change.before.PSObject.Properties[
      "assume_role_policy"
    ]
    $AfterPolicyProperty = $ResourceChange.change.after.PSObject.Properties[
      "assume_role_policy"
    ]
    if ($null -eq $BeforePolicyProperty -or $null -eq $AfterPolicyProperty) {
      throw "Plan does not contain both trust policy versions."
    }
    try {
      $BeforePolicy = ConvertFrom-Json -InputObject ([string]$BeforePolicyProperty.Value)
      $AfterPolicy = ConvertFrom-Json -InputObject ([string]$AfterPolicyProperty.Value)
    } catch {
      throw "Could not parse a planned trust policy; policy content was suppressed."
    }
    Assert-TrustPolicySubjectOnlyChange `
      $BeforePolicy `
      $AfterPolicy `
      $ExpectedBranch `
      "planned trust policy"
  }
}

function Get-TerraformPlanResources {
  param([AllowNull()][object]$Module)
  if ($null -eq $Module) {
    return
  }
  $ResourcesProperty = $Module.PSObject.Properties["resources"]
  if ($null -ne $ResourcesProperty) {
    foreach ($Resource in @($ResourcesProperty.Value)) {
      $Resource
    }
  }
  $ChildModulesProperty = $Module.PSObject.Properties["child_modules"]
  if ($null -ne $ChildModulesProperty) {
    foreach ($ChildModule in @($ChildModulesProperty.Value)) {
      Get-TerraformPlanResources $ChildModule
    }
  }
}

function Assert-TerraformPlanCallerAccount {
  param(
    [Parameter(Mandatory = $true)][string]$PlanJson,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $PlanDocument = ConvertFrom-Json -InputObject $PlanJson
  $PlannedValuesProperty = $PlanDocument.PSObject.Properties["planned_values"]
  if ($null -eq $PlannedValuesProperty) {
    throw "Terraform plan has no planned_values: $Label"
  }
  $RootModuleProperty = $PlannedValuesProperty.Value.PSObject.Properties["root_module"]
  if ($null -eq $RootModuleProperty) {
    throw "Terraform plan has no root_module: $Label"
  }
  $CallerIdentityResources = @(
    Get-TerraformPlanResources $RootModuleProperty.Value |
      Where-Object {
        $ModeProperty = $_.PSObject.Properties["mode"]
        $TypeProperty = $_.PSObject.Properties["type"]
        $null -ne $ModeProperty -and
        $null -ne $TypeProperty -and
        [string]$ModeProperty.Value -ceq "data" -and
        [string]$TypeProperty.Value -ceq "aws_caller_identity"
      }
  )
  if ($CallerIdentityResources.Count -lt 1) {
    throw "Terraform plan does not expose an aws_caller_identity data source: $Label"
  }
  $TerraformCallerAccounts = @(
    @(
      foreach ($CallerIdentityResource in $CallerIdentityResources) {
        $ValuesProperty = $CallerIdentityResource.PSObject.Properties["values"]
        if ($null -eq $ValuesProperty) {
          throw "Terraform caller identity has no values: $Label"
        }
        $AccountProperty = $ValuesProperty.Value.PSObject.Properties["account_id"]
        if (
          $null -eq $AccountProperty -or
          [string]$AccountProperty.Value -notmatch "^[0-9]{12}$"
        ) {
          throw "Terraform caller identity account is unavailable: $Label"
        }
        [string]$AccountProperty.Value
      }
    ) | Sort-Object -Unique
  )
  if (
    $TerraformCallerAccounts.Count -ne 1 -or
    $AllowedAccounts -notcontains $TerraformCallerAccounts[0] -or
    $TerraformCallerAccounts[0] -cne $ProfileCallerAccount
  ) {
    throw "Terraform provider caller account differs from the allowlisted selected profile account: $Label"
  }
  Write-Host "Terraform provider caller account allowlist: OK ($Label)"
}

$BootstrapPlan = Join-Path $CutoverDir "bootstrap-main.tfplan"
$BootstrapPlanLog = Join-Path $CutoverDir "bootstrap-main.plan.log"

Invoke-Terraform "-chdir=$BootstrapDir" plan -input=false `
  @BootstrapVarArgs `
  "-var=github_deploy_branch=$NewBranch" `
  "-target=aws_iam_role.terraform_plan" `
  "-target=aws_iam_role.terraform_lifecycle" `
  "-out=$BootstrapPlan" *> $BootstrapPlanLog
$BootstrapPlanExit = $LASTEXITCODE
Protect-CutoverFileIfPresent $BootstrapPlan
Protect-CutoverFileIfPresent $BootstrapPlanLog
if ($BootstrapPlanExit -ne 0) {
  throw "Bootstrap targeted plan failed. Review the local log without sharing identifiers."
}

$BootstrapPlanJson = (
  Invoke-Terraform "-chdir=$BootstrapDir" show -json $BootstrapPlan | Out-String
)
if ($LASTEXITCODE -ne 0) {
  throw "Could not inspect bootstrap saved plan."
}
Assert-TerraformPlanCallerAccount $BootstrapPlanJson "bootstrap cutover"
$BootstrapChanges = @(
  (ConvertFrom-Json -InputObject $BootstrapPlanJson).resource_changes |
    Where-Object { @($_.change.actions) -notcontains "no-op" }
)
$AllowedBootstrapAddresses = @(
  "aws_iam_role.terraform_plan",
  "aws_iam_role.terraform_lifecycle"
)
if ($BootstrapChanges.Count -gt 2) {
  throw "Bootstrap plan contains too many changes."
}
Assert-TrustOnlyPlanChanges $BootstrapChanges $AllowedBootstrapAddresses $NewBranch
Write-Host "Bootstrap approved resource changes: $($BootstrapChanges.Count)"
Write-Host "Privately inspect $BootstrapPlanLog and confirm only assume_role_policy changes to refs/heads/main."

if ($BootstrapChanges.Count -gt 0) {
  Assert-MainShaUnchanged "before bootstrap IAM update"
  $BootstrapApplyLog = Join-Path $CutoverDir "bootstrap-main.apply.log"
  Invoke-Terraform "-chdir=$BootstrapDir" apply -input=false $BootstrapPlan *> $BootstrapApplyLog
  $BootstrapApplyExit = $LASTEXITCODE
  Protect-CutoverFileIfPresent $BootstrapApplyLog
  if ($BootstrapApplyExit -ne 0) {
    throw "Bootstrap saved plan apply failed. Use the rollback section before retrying."
  }
}

$PlanBranchAfter = Backup-RoleTrustAndGetBranch "terraform-plan-after" $PlanRoleName
$LifecycleBranchAfter = Backup-RoleTrustAndGetBranch "terraform-lifecycle-after" $LifecycleRoleName
if ($PlanBranchAfter -cne $NewBranch -or $LifecycleBranchAfter -cne $NewBranch) {
  throw "Bootstrap trust verification failed after apply."
}
Write-Host "Bootstrap plan/lifecycle trust: main"
```

**期待される結果**

- 変更前が旧branchなら、`aws_iam_role.terraform_plan`と`aws_iam_role.terraform_lifecycle`だけが`update`になる。
- apply完了後、両方のbranch検証が`main`になる。この時点で`main`からplan roleとlifecycle roleのAssumeRoleが可能になる。

**失敗時**

- plan段階の失敗は変更なし。入力、権限、権威あるstateを修正してplanから再開する。
- 想定外address、create、delete、replace、trust以外の属性差分があればapplyしない。
- applyがpartial failureした場合、2 roleを個別に再確認する。どちらかが`main`なら、残りだけを同じsaved-plan手順で再planするか、ロールバック節で各roleを記録済みの開始時policyへ戻す。
- `terraform.tfstate`を手で編集しない。local state破損時は`$BootstrapStateBackup`を保全し、AWS trustの緊急復旧を先に行う。

### 手順3: root deploy roleを更新

**cwd:** 任意。rootは`$RootDir`とremote backendを使う。

```powershell
$RootPlan = Join-Path $CutoverDir "root-deploy-main.tfplan"
$RootPlanLog = Join-Path $CutoverDir "root-deploy-main.plan.log"

Invoke-Terraform "-chdir=$RootDir" plan -input=false `
  "-var-file=$RootTfvars" `
  "-var=github_deploy_branch=$NewBranch" `
  "-target=module.iam.aws_iam_role.github_deploy" `
  "-out=$RootPlan" *> $RootPlanLog
$RootPlanExit = $LASTEXITCODE
Protect-CutoverFileIfPresent $RootPlan
Protect-CutoverFileIfPresent $RootPlanLog
if ($RootPlanExit -ne 0) {
  throw "Root targeted plan failed. Review the local log without sharing identifiers."
}

$RootPlanJson = (
  Invoke-Terraform "-chdir=$RootDir" show -json $RootPlan | Out-String
)
if ($LASTEXITCODE -ne 0) {
  throw "Could not inspect root saved plan."
}
Assert-TerraformPlanCallerAccount $RootPlanJson "root cutover"
$RootChanges = @(
  (ConvertFrom-Json -InputObject $RootPlanJson).resource_changes |
    Where-Object { @($_.change.actions) -notcontains "no-op" }
)
if ($RootChanges.Count -gt 1) {
  throw "Root plan contains too many changes."
}
Assert-TrustOnlyPlanChanges `
  $RootChanges `
  @("module.iam.aws_iam_role.github_deploy") `
  $NewBranch
Write-Host "Root approved resource changes: $($RootChanges.Count)"
Write-Host "Privately inspect $RootPlanLog and confirm only assume_role_policy changes to refs/heads/main."

if ($RootChanges.Count -gt 0) {
  Assert-MainShaUnchanged "before root IAM update"
  $RootApplyLog = Join-Path $CutoverDir "root-deploy-main.apply.log"
  Invoke-Terraform "-chdir=$RootDir" apply -input=false $RootPlan *> $RootApplyLog
  $RootApplyExit = $LASTEXITCODE
  Protect-CutoverFileIfPresent $RootApplyLog
  if ($RootApplyExit -ne 0) {
    throw "Root saved plan apply failed. Use the rollback section before retrying."
  }
}

$DeployBranchAfter = Backup-RoleTrustAndGetBranch "github-deploy-after" $DeployRoleName
if ($DeployBranchAfter -cne $NewBranch) {
  throw "Deploy role trust verification failed after apply."
}
Write-Host "Root deploy trust: main"
```

**期待される結果**

- 変更前が旧branchなら`module.iam.aws_iam_role.github_deploy`だけがin-place updateになる。
- apply後のbranch検証が`main`になる。この時点で`main`からapp/frontend deploy roleのAssumeRoleが可能になる。

**失敗時**

- plan失敗または想定外差分ではapplyしない。直近正常applyと同一のroot tfvars、remote backend、local権限を確認する。
- apply失敗後はrole trustを再確認する。`main`でなければ同じplanを再利用せず、新しいplanを作る。
- root stack全体の差分をこの手順でapplyしない。無関係なdriftは別issueへ分離する。

### 手順4: OIDC smoke roleを更新

**cwd:** 任意。手順1で作成したbackupを使う。

既存smoke roleのtrustが期待と違う場合、`aws-oidc-bootstrap.ps1`は`deploy/aws-ecs/scripts/aws-oidc-bootstrap.ps1:248-254`でfail closedする。そのため、review済みbackupのsubject一箇所だけを書き換え、local IAM権限で直接更新する。

```powershell
$SmokeBackup = Join-Path $CutoverDir "oidc-smoke.before.json"
$SmokeMainPolicy = Join-Path $CutoverDir "oidc-smoke.main.json"
$SmokePolicy = Get-Content -LiteralPath $SmokeBackup -Raw | ConvertFrom-Json
$SmokeStatements = @($SmokePolicy.Statement)
if ($SmokeStatements.Count -ne 1) {
  throw "Unexpected smoke trust statement count."
}
$SmokeConditionProperty = $SmokeStatements[0].PSObject.Properties["Condition"]
if ($null -eq $SmokeConditionProperty) {
  throw "Smoke trust has no Condition."
}
$SmokeCondition = $SmokeConditionProperty.Value
$SmokeAudience = @(
  Get-OidcConditionValues $SmokeCondition "StringEquals" $OidcAudienceKey
)
$SmokeSubjectProperty = Get-RequiredOidcSubjectProperty `
  $SmokeCondition `
  "oidc-smoke"
$SmokeSubjectValues = @($SmokeSubjectProperty.Value)
$ExpectedOldSubject = "repo:${Repository}:ref:refs/heads/${OldBranch}"
$ExpectedNewSubject = "repo:${Repository}:ref:refs/heads/${NewBranch}"
if ($SmokeAudience.Count -ne 1 -or [string]$SmokeAudience[0] -cne "sts.amazonaws.com") {
  throw "Unexpected smoke trust audience."
}
if (
  $SmokeSubjectValues.Count -ne 1 -or
  [string]$SmokeSubjectValues[0] -notin @($ExpectedOldSubject, $ExpectedNewSubject)
) {
  throw "Unexpected smoke trust subject."
}
$SmokeSubject = [string]$SmokeSubjectValues[0]

if ($SmokeSubject -cne $ExpectedNewSubject) {
  Assert-MainShaUnchanged "before OIDC smoke IAM update"
  Write-RoleTrustPolicyForBranch `
    $SmokeBackup `
    $SmokeMainPolicy `
    $NewBranch `
    "oidc-smoke"
  aws iam update-assume-role-policy `
    --profile $AwsProfile `
    --role-name $SmokeRoleName `
    --policy-document "file://$SmokeMainPolicy" `
    --no-cli-pager
  if ($LASTEXITCODE -ne 0) {
    throw "Smoke trust update failed. Restore the backup policy if verification is not main."
  }
}

$SmokeBranchAfter = Backup-RoleTrustAndGetBranch "oidc-smoke-after" $SmokeRoleName
if ($SmokeBranchAfter -cne $NewBranch) {
  throw "Smoke role trust verification failed after update."
}
Write-Host "OIDC smoke trust: main"
```

**期待される結果**

- IAM updateは既存1 statementのsubjectだけを`refs/heads/main`へ変える。
- 生成policyはbackupのsubjectだけを置換した期待値と完全一致し、provider、Action、audience、その他のconditionを保持する。
- branch検証が`main`になる。この時点で`main`からpermissionless smoke roleのAssumeRoleが可能になる。

**失敗時**

- policy shapeまたはsubjectが想定外なら更新しない。
- update後の検証が`main`でなければ、後述のロールバックでbackupから記録済みの開始時policyへ戻す。
- provider、audience、permission policy、role名を同時に変更しない。

### 手順5: `DEPLOY_BRANCH`を`main`へ変更

**cwd:** `$MainRepoRoot`

この手順より前に4 roleすべてが`main`であることを再確認する。

```powershell
Set-Location -LiteralPath $MainRepoRoot
$TrustBranches = @(
  Backup-RoleTrustAndGetBranch "terraform-plan-pre-variable" $PlanRoleName
  Backup-RoleTrustAndGetBranch "terraform-lifecycle-pre-variable" $LifecycleRoleName
  Backup-RoleTrustAndGetBranch "github-deploy-pre-variable" $DeployRoleName
  Backup-RoleTrustAndGetBranch "oidc-smoke-pre-variable" $SmokeRoleName
)
if (@($TrustBranches | Where-Object { $_ -cne $NewBranch }).Count -ne 0) {
  throw "All four role trusts must be main before changing DEPLOY_BRANCH."
}

gh variable set DEPLOY_BRANCH --body $NewBranch
if ($LASTEXITCODE -ne 0) {
  throw "DEPLOY_BRANCH update failed. IAM trusts are already main; do not run workflows until this is fixed."
}
$DeployBranchAfter = Get-DeployBranchVariable
if ($DeployBranchAfter -cne $NewBranch) {
  throw "DEPLOY_BRANCH verification failed."
}
Write-Host "DEPLOY_BRANCH: main"
```

**期待される結果**

- `DEPLOY_BRANCH`のread-backが`main`になる。
- この時点で3系統のtrust、main-only workflow guard、Terraform入力が整合する。

**失敗時**

- IAM trustを直ちに戻す必要はない。workflowを実行せず、repository variable更新権限と値を修正する。
- 修正できず開始時状態へ戻す場合は、ロールバック節の逆順手順を使う。

### 手順6: `main`からOIDC smokeを実行

**cwd:** `$MainRepoRoot`

```powershell
Set-Location -LiteralPath $MainRepoRoot
Assert-MainShaUnchanged "before OIDC smoke dispatch"
$DispatchTime = (Get-Date).ToUniversalTime()
gh workflow run aws-oidc-smoke.yml --ref main
if ($LASTEXITCODE -ne 0) {
  throw "OIDC smoke dispatch failed."
}

$SmokeRun = $null
for ($Attempt = 0; $Attempt -lt 12 -and $null -eq $SmokeRun; $Attempt++) {
  Start-Sleep -Seconds 5
  $RunsJson = (
    gh run list `
      --workflow aws-oidc-smoke.yml `
      --branch main `
      --event workflow_dispatch `
      --limit 10 `
      --json databaseId,createdAt,headBranch,headSha,status,conclusion |
      Out-String
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not list OIDC smoke runs."
  }
  $SmokeRun = @(
    (ConvertFrom-Json -InputObject $RunsJson) |
      Where-Object {
        $_.headBranch -ceq "main" -and
        $_.headSha -ceq $MainSha -and
        ([datetime]$_.createdAt).ToUniversalTime() -ge $DispatchTime.AddSeconds(-5)
      } |
      Sort-Object createdAt -Descending
  ) | Select-Object -First 1
}
if ($null -eq $SmokeRun) {
  throw "Dispatched OIDC smoke run was not found."
}

gh run watch ([string]$SmokeRun.databaseId) --exit-status
if ($LASTEXITCODE -ne 0) {
  throw "OIDC smoke did not complete successfully. Do not dispatch lifecycle workflows."
}
$SmokeResultJson = (
  gh run view ([string]$SmokeRun.databaseId) `
    --json headBranch,headSha,status,conclusion |
    Out-String
)
$SmokeResult = ConvertFrom-Json -InputObject $SmokeResultJson
if (
  $SmokeResult.headBranch -cne "main" -or
  $SmokeResult.headSha -cne $MainSha -or
  $SmokeResult.status -cne "completed" -or
  $SmokeResult.conclusion -cne "success"
) {
  throw "OIDC smoke result did not meet the completion criteria."
}
Write-Host "main OIDC smoke: success"
```

**期待される結果**

- runの`headBranch`が`main`、`headSha`が開始時の`$MainSha`、`status`が`completed`、`conclusion`が`success`になる。
- `Configure permissionless OIDC credentials`を通過し、permissionless verifierが成功する。

**失敗時**

- 自動rerunしない。4 trustと`DEPLOY_BRANCH`をbranch名だけで再確認する。
- dispatch直前のSHA検証またはrunの`headSha`照合が失敗した場合、更新後の`main`を再dispatchしない。既に完了したIAM / repository variable更新を開始時状態へrollbackする。
- `Configure permissionless OIDC credentials`で失敗した場合はsmoke role trust、GitHub secretのrole対応、repository/refを確認する。
- verifierで失敗した場合はaccount allowlistまたはsecretのrole対応を確認する。account ID、ARN、tokenをissueやlogへ転記しない。
- 原因を安全に解消できない場合は、ロールバック節で旧状態へ戻す。

### 手順7: 旧branch依存の消滅とtarget後の全体planを確認

**cwd:** `$MainRepoRoot`

```powershell
$FinalBranches = [ordered]@{
  terraform_plan = Backup-RoleTrustAndGetBranch "terraform-plan-final" $PlanRoleName
  terraform_lifecycle = Backup-RoleTrustAndGetBranch "terraform-lifecycle-final" $LifecycleRoleName
  github_deploy = Backup-RoleTrustAndGetBranch "github-deploy-final" $DeployRoleName
  oidc_smoke = Backup-RoleTrustAndGetBranch "oidc-smoke-final" $SmokeRoleName
}
if (@($FinalBranches.Values | Where-Object { $_ -cne $NewBranch }).Count -ne 0) {
  throw "At least one role does not trust main."
}
if ((Get-DeployBranchVariable) -cne $NewBranch) {
  throw "DEPLOY_BRANCH is not main."
}

Set-Location -LiteralPath $MainRepoRoot
Assert-MainShaUnchanged "before final operational inspection"
$OperationalOldBranchMatches = @(
  git grep -n "deploy/AWS_ECS" -- `
    ".github/workflows/*.yml" `
    "deploy/aws-ecs/*.tf" `
    ":(glob)deploy/aws-ecs/**/*.tf" `
    ":(glob)deploy/aws-ecs/scripts/*.ps1"
)
if ($OperationalOldBranchMatches.Count -ne 0) {
  $OperationalOldBranchMatches
  throw "An operational old-branch dependency remains."
}
Write-Host "Operational old-branch references: none"

$BootstrapPostPlan = Join-Path $CutoverDir "bootstrap-post-cutover.tfplan"
$BootstrapPostLog = Join-Path $CutoverDir "bootstrap-post-cutover.plan.log"
Invoke-Terraform "-chdir=$BootstrapDir" plan -input=false `
  @BootstrapVarArgs `
  "-var=github_deploy_branch=$NewBranch" `
  "-out=$BootstrapPostPlan" `
  -detailed-exitcode *> $BootstrapPostLog
$BootstrapPostExit = $LASTEXITCODE
Protect-CutoverFileIfPresent $BootstrapPostPlan
Protect-CutoverFileIfPresent $BootstrapPostLog
if ($BootstrapPostExit -eq 1) {
  throw "Bootstrap post-cutover full plan failed."
}
$BootstrapPostPlanJson = (
  Invoke-Terraform "-chdir=$BootstrapDir" show -json $BootstrapPostPlan | Out-String
)
if ($LASTEXITCODE -ne 0) {
  throw "Could not inspect bootstrap post-cutover full plan."
}
Assert-TerraformPlanCallerAccount $BootstrapPostPlanJson "bootstrap post-cutover full plan"
$BootstrapPostChanges = @(
  (ConvertFrom-Json -InputObject $BootstrapPostPlanJson).resource_changes |
    Where-Object { @($_.change.actions) -notcontains "no-op" }
)
$BootstrapPostTrustChanges = @(
  $BootstrapPostChanges |
    Where-Object { $_.address -in $AllowedBootstrapAddresses }
)
Assert-TrustOnlyPlanChanges `
  $BootstrapPostTrustChanges `
  $AllowedBootstrapAddresses `
  $NewBranch

$RootPostPlan = Join-Path $CutoverDir "root-post-cutover.tfplan"
$RootPostLog = Join-Path $CutoverDir "root-post-cutover.plan.log"
Invoke-Terraform "-chdir=$RootDir" plan -input=false `
  "-var-file=$RootTfvars" `
  "-var=github_deploy_branch=$NewBranch" `
  "-out=$RootPostPlan" `
  -detailed-exitcode *> $RootPostLog
$RootPostExit = $LASTEXITCODE
Protect-CutoverFileIfPresent $RootPostPlan
Protect-CutoverFileIfPresent $RootPostLog
if ($RootPostExit -eq 1) {
  throw "Root post-cutover full plan failed."
}
$RootPostPlanJson = (
  Invoke-Terraform "-chdir=$RootDir" show -json $RootPostPlan | Out-String
)
if ($LASTEXITCODE -ne 0) {
  throw "Could not inspect root post-cutover full plan."
}
Assert-TerraformPlanCallerAccount $RootPostPlanJson "root post-cutover full plan"
$RootPostChanges = @(
  (ConvertFrom-Json -InputObject $RootPostPlanJson).resource_changes |
    Where-Object { @($_.change.actions) -notcontains "no-op" }
)
$RootPostTrustChanges = @(
  $RootPostChanges |
    Where-Object { $_.address -ceq "module.iam.aws_iam_role.github_deploy" }
)
Assert-TrustOnlyPlanChanges `
  $RootPostTrustChanges `
  @("module.iam.aws_iam_role.github_deploy") `
  $NewBranch
Assert-MainShaUnchanged "after final operational inspection"
Write-Host "Post-cutover full plan exit codes: bootstrap=$BootstrapPostExit root=$RootPostExit"
Write-Host (
  "Post-cutover trust changes: bootstrap={0} root={1}" -f
  $BootstrapPostTrustChanges.Count,
  $RootPostTrustChanges.Count
)
```

**期待される結果**

- 4 roleのbranchがすべて`main`、`DEPLOY_BRANCH`も`main`になる。
- operational fileに旧branch参照がない。歴史説明を持つdocs内の旧branch名は対象外である。
- 通常planの`-detailed-exitcode`は、差分なしなら`0`、差分ありなら`2`である。
- Terraform管理のtrust roleに差分がある場合は、targeted saved planと同じsubject-only完全比較を通る。

**失敗時**

- full planが`2`でも、この手順ではapplyしない。local logを安全な場所でreviewし、targetingで見落としたtrust関連差分ならD1a内で修正、無関係なdriftなら別issueへ分離する。
- trust roleの差分がsubject-only完全比較に失敗した場合は、providerや追加conditionなどの値を表示せず停止する。
- full planが`1`ならbackend、tfvars、権限を確認する。trustとsmokeの個別検証が成功済みでも、D1a完了チェックには失敗として記録する。
- 一時ファイルはrollback判断が完了するまで削除しない。

### 手順8: 一時artifactを安全に削除

**cwd:** 任意。手順7とsmokeが成功し、rollbackしないと人間が判断した後だけ実行する。

`$CutoverDir`にはstate backup、saved plan、plan log、実trust policyが含まれる。長期保管せず、exact pathがOSの一時directory配下かつこのrunbookのprefixであることを検証してから削除する。

```powershell
try {
  $ResolvedCutoverDir = (Resolve-Path -LiteralPath $CutoverDir).Path
  $ResolvedTempDir = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
  $CutoverLeaf = Split-Path -Leaf $ResolvedCutoverDir
  if (
    -not $ResolvedCutoverDir.StartsWith($ResolvedTempDir, [StringComparison]::OrdinalIgnoreCase) -or
    $CutoverLeaf -notmatch "^ragproject-trust-cutover-[0-9]{8}-[0-9]{6}$"
  ) {
    throw "Refusing to delete an unexpected path."
  }
  Remove-Item -LiteralPath $ResolvedCutoverDir -Recurse -Force
  if (Test-Path -LiteralPath $ResolvedCutoverDir) {
    throw "Cutover artifact cleanup failed."
  }
  Write-Host "Cutover artifacts: removed"
} finally {
  Restore-OriginalAwsProfile
}
Write-Host "AWS_PROFILE: restored to the process start state"
```

**期待される結果**

- 検証済みの一時directoryだけが削除される。
- repository内のfile、Terraform remote state、AWS resourceは削除されない。
- process-scoped `AWS_PROFILE`はrunbook開始時の値へ戻り、開始時に未設定なら削除される。

**失敗時**

- path validation失敗時は削除commandを変更して強行しない。pathを再確認する。
- rollbackの可能性が残る場合は削除せず、アクセス制限されたlocal領域で短期間保全する。

## 6. ロールバック

### 6.1 段階別の判断

| 失敗時点 | 戻す対象 | 手順 |
|---|---|---|
| 手順1 | なし | AWS変更前。原因解消まで停止 |
| 手順2の途中 | bootstrap plan / lifecycle role | 同じlocal stateから各roleの`$BeforeBranches`を個別に指定したsaved planをapply。state利用不能なら各`*.before.json`を直接復元 |
| 手順3の途中 | root deploy role、その後bootstrap 2 role | root、bootstrapの順に、それぞれ記録済みの開始時branchへ戻す |
| 手順4の途中 | smoke、root、bootstrap | smokeの`oidc-smoke.before.json`を復元し、root、bootstrapも各開始時branchへ戻す |
| 手順5以後 | repository variable、smoke、root、bootstrap | `DEPLOY_BRANCH`を`$CurrentDeployBranch`へ戻してから、roleを逆順で各開始時branchへ戻す |
| 手順6 smoke失敗 | 原因に応じて継続または全rollback | workflowは再実行せず、trust / secret対応 / allowlistを確認 |

rollbackの基準は旧branchではなく、手順1で記録した開始時状態である。preferred rollbackはTerraform stateを使い、root / bootstrapの各roleをそれぞれの`$BeforeBranches`へ個別に戻す。開始時に`main`だったroleはrollback後も`main`のままであり、旧branchへ強制してはならない。backupを直接適用する経路も、現在policyのsubjectだけを記録済みbranchへ置換した期待値とbackup全体を比較し、一致しなければ適用前に停止する。

```powershell
function Restore-RoleTrust {
  param(
    [Parameter(Mandatory = $true)][string]$RoleName,
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$ExpectedBranch
  )
  if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw "Trust backup not found."
  }
  $CurrentRoleJson = (
    aws iam get-role --profile $AwsProfile --role-name $RoleName --output json --no-cli-pager |
      Out-String
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not read the current trust before restore."
  }
  try {
    $CurrentPolicy = (
      ConvertFrom-Json -InputObject $CurrentRoleJson
    ).Role.AssumeRolePolicyDocument
    $BackupPolicy = Get-Content -LiteralPath $BackupPath -Raw | ConvertFrom-Json
  } catch {
    throw "Could not parse a trust policy before restore; policy content was suppressed."
  }
  Assert-TrustPolicySubjectOnlyChange `
    $CurrentPolicy `
    $BackupPolicy `
    $ExpectedBranch `
    "direct trust restore"
  aws iam update-assume-role-policy `
    --profile $AwsProfile `
    --role-name $RoleName `
    --policy-document "file://$BackupPath" `
    --no-cli-pager
  if ($LASTEXITCODE -ne 0) {
    throw "Trust restore failed."
  }
}

if ([string]$env:AWS_PROFILE -cne $AwsProfile) {
  throw "The validated AWS_PROFILE is not active. Restore the runbook session before rollback."
}

gh variable set DEPLOY_BRANCH --body $CurrentDeployBranch
$DeployBranchRollbackOk = $LASTEXITCODE -eq 0
if (-not $DeployBranchRollbackOk) {
  Write-Warning "Could not restore DEPLOY_BRANCH. Keep workflows stopped and continue IAM recovery."
}

$SmokeBeforePath = Join-Path $CutoverDir "oidc-smoke.before.json"
Restore-RoleTrust $SmokeRoleName $SmokeBeforePath $BeforeBranches.oidc_smoke
if (
  (Backup-RoleTrustAndGetBranch "oidc-smoke-rollback-check" $SmokeRoleName) -cne
  $BeforeBranches.oidc_smoke
) {
  throw "Smoke rollback verification failed."
}

function Restore-TerraformRoleToRecordedBranch {
  param(
    [Parameter(Mandatory = $true)][string]$TerraformDirectory,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$TerraformVarArguments,
    [Parameter(Mandatory = $true)][string]$ResourceAddress,
    [Parameter(Mandatory = $true)][string]$RoleName,
    [Parameter(Mandatory = $true)][string]$ExpectedBranch,
    [Parameter(Mandatory = $true)][string]$ArtifactLabel
  )
  if ($ExpectedBranch -notin @($BeforeBranches.Values)) {
    throw "Rollback branch is not one of the recorded role start values: $ArtifactLabel"
  }
  $RollbackPlan = Join-Path $CutoverDir "$ArtifactLabel.before.tfplan"
  $RollbackPlanLog = Join-Path $CutoverDir "$ArtifactLabel.before.plan.log"
  Invoke-Terraform "-chdir=$TerraformDirectory" plan -input=false `
    @TerraformVarArguments `
    "-var=github_deploy_branch=$ExpectedBranch" `
    "-target=$ResourceAddress" `
    "-out=$RollbackPlan" *> $RollbackPlanLog
  $RollbackPlanExit = $LASTEXITCODE
  Protect-CutoverFileIfPresent $RollbackPlan
  Protect-CutoverFileIfPresent $RollbackPlanLog
  if ($RollbackPlanExit -ne 0) {
    throw "Recorded-state rollback plan failed: $ArtifactLabel"
  }
  $RollbackPlanJson = (
    Invoke-Terraform "-chdir=$TerraformDirectory" show -json $RollbackPlan | Out-String
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect recorded-state rollback plan: $ArtifactLabel"
  }
  Assert-TerraformPlanCallerAccount $RollbackPlanJson "$ArtifactLabel rollback"
  $RollbackChanges = @(
    (ConvertFrom-Json -InputObject $RollbackPlanJson).resource_changes |
      Where-Object { @($_.change.actions) -notcontains "no-op" }
  )
  if ($RollbackChanges.Count -gt 1) {
    throw "Recorded-state rollback plan contains too many changes: $ArtifactLabel"
  }
  Assert-TrustOnlyPlanChanges `
    $RollbackChanges `
    @($ResourceAddress) `
    $ExpectedBranch
  if ($RollbackChanges.Count -gt 0) {
    $RollbackApplyLog = Join-Path $CutoverDir "$ArtifactLabel.before.apply.log"
    Invoke-Terraform "-chdir=$TerraformDirectory" apply -input=false $RollbackPlan *> $RollbackApplyLog
    $RollbackApplyExit = $LASTEXITCODE
    Protect-CutoverFileIfPresent $RollbackApplyLog
    if ($RollbackApplyExit -ne 0) {
      throw "Recorded-state rollback apply failed: $ArtifactLabel"
    }
  }
  if (
    (Backup-RoleTrustAndGetBranch "$ArtifactLabel-rollback-check" $RoleName) -cne
    $ExpectedBranch
  ) {
    throw "Recorded-state rollback verification failed: $ArtifactLabel"
  }
}

Restore-TerraformRoleToRecordedBranch `
  -TerraformDirectory $RootDir `
  -TerraformVarArguments @("-var-file=$RootTfvars") `
  -ResourceAddress "module.iam.aws_iam_role.github_deploy" `
  -RoleName $DeployRoleName `
  -ExpectedBranch $BeforeBranches.github_deploy `
  -ArtifactLabel "github-deploy"

Restore-TerraformRoleToRecordedBranch `
  -TerraformDirectory $BootstrapDir `
  -TerraformVarArguments $BootstrapVarArgs `
  -ResourceAddress "aws_iam_role.terraform_lifecycle" `
  -RoleName $LifecycleRoleName `
  -ExpectedBranch $BeforeBranches.terraform_lifecycle `
  -ArtifactLabel "terraform-lifecycle"

Restore-TerraformRoleToRecordedBranch `
  -TerraformDirectory $BootstrapDir `
  -TerraformVarArguments $BootstrapVarArgs `
  -ResourceAddress "aws_iam_role.terraform_plan" `
  -RoleName $PlanRoleName `
  -ExpectedBranch $BeforeBranches.terraform_plan `
  -ArtifactLabel "terraform-plan"

if (-not $DeployBranchRollbackOk) {
  Restore-OriginalAwsProfile
  throw "IAM rollback succeeded, but DEPLOY_BRANCH still requires manual restoration. Keep workflows stopped."
}
Restore-OriginalAwsProfile
Write-Host "Rollback: all roles and DEPLOY_BRANCH restored to their recorded start values"
Write-Host "AWS_PROFILE: restored to the process start state"
```

rollback後もD0のmain-only workflow guardは変わらない。開始時`main`だったroleは`main`のまま、開始時が旧branchだったroleだけが旧branchへ戻る。旧branch workflowを運用経路として再開せず、再cutoverまでAWS workflowを停止する。

### 6.2 `main`からも旧branchからもAssumeRoleできない場合

GitHub OIDC経路を復旧手段に使わない。local AWS認証はGitHub branch trustから独立しているため、次の順で復旧する。

1. workflowを実行しない。`DEPLOY_BRANCH`を`$CurrentDeployBranch`へ戻せるなら先に戻す。
2. preflightで作成した4 trust backupとbootstrap state backupが残っていることを確認する。中身は表示しない。
3. `oidc-smoke.before.json`をそのままsmoke roleへ適用し、`$BeforeBranches.oidc_smoke`と照合する。
4. root remote stateが利用可能なら、preferred rollbackのtargeted saved planでdeploy roleを`$BeforeBranches.github_deploy`へ戻す。
5. bootstrap local stateが利用可能なら、preferred rollbackのrole別targeted saved planでplan / lifecycle roleをそれぞれの`$BeforeBranches`へ戻す。
6. Terraform stateが利用不能、lockが解消できない、またはapplyが途中で止まる場合は、各`*.before.json`をlocal IAM権限で直接適用し、roleごとの`$BeforeBranches`と照合する。

緊急直接復旧は次のとおりである。

```powershell
if ([string]$env:AWS_PROFILE -cne $AwsProfile) {
  throw "The validated AWS_PROFILE is not active. Restore the runbook session before direct recovery."
}

gh variable set DEPLOY_BRANCH --body $CurrentDeployBranch
$DirectDeployBranchRecoveryOk = $LASTEXITCODE -eq 0
if (-not $DirectDeployBranchRecoveryOk) {
  Write-Warning "Could not restore DEPLOY_BRANCH. Keep workflows stopped and continue direct IAM recovery."
}

$DirectRecoveryRoles = @(
  [pscustomobject]@{
    Label = "oidc-smoke"
    RoleName = $SmokeRoleName
    ExpectedBranch = $BeforeBranches.oidc_smoke
  }
  [pscustomobject]@{
    Label = "github-deploy"
    RoleName = $DeployRoleName
    ExpectedBranch = $BeforeBranches.github_deploy
  }
  [pscustomobject]@{
    Label = "terraform-lifecycle"
    RoleName = $LifecycleRoleName
    ExpectedBranch = $BeforeBranches.terraform_lifecycle
  }
  [pscustomobject]@{
    Label = "terraform-plan"
    RoleName = $PlanRoleName
    ExpectedBranch = $BeforeBranches.terraform_plan
  }
)
foreach ($RecoveryRole in $DirectRecoveryRoles) {
  $BackupPath = Join-Path $CutoverDir "$($RecoveryRole.Label).before.json"
  Restore-RoleTrust `
    $RecoveryRole.RoleName `
    $BackupPath `
    $RecoveryRole.ExpectedBranch
  if (
    (Backup-RoleTrustAndGetBranch "$($RecoveryRole.Label)-recovered" $RecoveryRole.RoleName) -cne
    $RecoveryRole.ExpectedBranch
  ) {
    throw "Emergency trust recovery does not match the recorded start value: $($RecoveryRole.Label)"
  }
}
if (-not $DirectDeployBranchRecoveryOk) {
  Restore-OriginalAwsProfile
  throw "Emergency IAM recovery succeeded, but DEPLOY_BRANCH still requires manual restoration."
}
Restore-OriginalAwsProfile
Write-Host "Emergency recovery: all roles and DEPLOY_BRANCH restored to their recorded start values"
Write-Host "AWS_PROFILE: restored to the process start state"
```

backupにはprovider識別子が含まれるため、terminalへ出力せず`file://`で渡す。開始時`main`だったroleは緊急直接復旧後も`main`であることを期待結果とする。backupが失われ、かつTerraform stateも利用不能なら、推測でpolicyを再作成しない。IAM管理者による別レビューとincident扱いに切り替える。

## 7. 完了チェックリスト

- [ ] PR #126 commitが実行用`main`の祖先である
- [ ] 権威あるbootstrap local stateとroot remote stateを特定した
- [ ] 直近applyと同一のbootstrap/root入力を使用した
- [ ] Terraform plan roleのsubjectが`refs/heads/main`だけである
- [ ] Terraform lifecycle roleのsubjectが`refs/heads/main`だけである
- [ ] root GitHub deploy roleのsubjectが`refs/heads/main`だけである
- [ ] permissionless OIDC smoke roleのsubjectが`refs/heads/main`だけである
- [ ] audienceは4 roleとも`sts.amazonaws.com`のままである
- [ ] provider principal、permission policy、role名を変更していない
- [ ] repository variable `DEPLOY_BRANCH`が`main`である
- [ ] cutover中の全checkpointで`origin/main`が開始時の`$MainSha`から動いていない
- [ ] `main`から開始時の`$MainSha`で実行した`AWS OIDC Smoke`がsuccessである
- [ ] operational `.yml` / `.tf` / `.ps1`に旧branch依存がない
- [ ] bootstrap/rootのpost-cutover full planが成功し、exit codeと未適用driftを記録した
- [ ] saved plan内のTerraform provider caller accountが明示profile accountと一致し、allowlist内である
- [ ] account ID、ARN、state、plan、trust backup、secret、tokenをissue / PR / chatへ貼っていない
- [ ] rollback判断が終わるまで`$CutoverDir`を保全した
- [ ] rollback不要の判断後、手順8で一時artifactを削除した
- [ ] process-scoped `AWS_PROFILE`をrunbook開始時の値へ戻した

全項目を満たした時点でD1aは完了である。履歴説明のdocsに残る旧branch文字列は、運用依存ではないため削除しない。

## 8. D1b（RAG-22）への引き継ぎ

D1a完了後は、`main`のworkflow guard、3系統のOIDC trust、`DEPLOY_BRANCH`が一致するため、D1b（RAG-22）で`main`からAWS lifecycleを安全に実測できる。D1bでは、review済みplanを使ったephemeral runtimeのup / smoke / down、残存resource確認、コストと時間の記録を行える。D1aはその認証経路を整えるだけであり、runtime lifecycle自体は実行しない。

## 9. 参考

- [AWS base branch decision](./aws_base_branch_decision.md)
- [AWS ECS deploy guide](../../deploy/aws-ecs/DEPLOY.md)
- [Terraform backend configuration](https://developer.hashicorp.com/terraform/language/backend)
- [Terraform saved plan workflow](https://developer.hashicorp.com/terraform/cli/commands/plan#out-filename)
- [Terraform resource targeting](https://developer.hashicorp.com/terraform/tutorials/state/resource-targeting)
