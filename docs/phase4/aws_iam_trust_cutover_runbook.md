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

trust policyを変更しても、既に発行されたSTS sessionは失効せず、有効期限までAWS resourceを更新できる。さらに旧branchと`main`のconcurrency groupは相互排他ではないため、手順2へ進む直前に両branchの関連runが停止していることを必ず確認する。

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
$RootDir = (Resolve-Path -LiteralPath (Join-Path $MainRepoRoot "deploy/aws-ecs")).Path
$RootTfvars = (Resolve-Path -LiteralPath $RootTfvars).Path
if ($BootstrapTfvars) {
  $BootstrapTfvars = (Resolve-Path -LiteralPath $BootstrapTfvars).Path
}

foreach ($CommandName in @("git", "terraform", "aws", "gh")) {
  if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
    throw "Required command is unavailable: $CommandName"
  }
}

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

function Invoke-NativeCommand {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments
  )
  $NativeErrorPreferenceWasSet = Test-Path Variable:PSNativeCommandUseErrorActionPreference
  $OriginalNativeErrorPreference = if ($NativeErrorPreferenceWasSet) {
    $PSNativeCommandUseErrorActionPreference
  } else {
    $null
  }
  try {
    if ($NativeErrorPreferenceWasSet) {
      $PSNativeCommandUseErrorActionPreference = $false
    }
    $Output = @(& $Command @Arguments)
    $ExitCode = $LASTEXITCODE
  } finally {
    if ($NativeErrorPreferenceWasSet) {
      $PSNativeCommandUseErrorActionPreference = $OriginalNativeErrorPreference
    }
  }
  return [pscustomobject]@{
    ExitCode = $ExitCode
    Output = $Output
  }
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
    $ProtectResult = Invoke-NativeCommand `
      -Command "icacls.exe" `
      -Arguments @($Path, "/inheritance:r", "/grant:r", $AclGrant)
  } else {
    $Mode = if ($Kind -ceq "Directory") { "700" } else { "600" }
    $ProtectResult = Invoke-NativeCommand `
      -Command "chmod" `
      -Arguments @($Mode, "--", $Path)
  }
  if ($ProtectResult.ExitCode -ne 0) {
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

function New-CutoverArtifactId {
  "{0}-{1}" -f
    (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffZ"),
    [guid]::NewGuid().ToString("N")
}

function Assert-CutoverArtifactPathsUnused {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Paths
  )
  foreach ($Path in $Paths) {
    if (Test-Path -LiteralPath $Path) {
      throw "Refusing to overwrite a cutover artifact: $(Split-Path -Leaf $Path)"
    }
  }
}

function Write-ProtectedCutoverTextOnce {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
  )
  $Bytes = [Text.UTF8Encoding]::new($false).GetBytes($Content)
  $Stream = [IO.File]::Open(
    $Path,
    [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write,
    [IO.FileShare]::None
  )
  try {
    $Stream.Write($Bytes, 0, $Bytes.Length)
  } finally {
    $Stream.Dispose()
  }
  Protect-CutoverPath $Path "File"
}

function Copy-ProtectedCutoverFileOnce {
  param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][string]$DestinationPath
  )
  $SourceStream = [IO.File]::OpenRead($SourcePath)
  try {
    $DestinationStream = [IO.File]::Open(
      $DestinationPath,
      [IO.FileMode]::CreateNew,
      [IO.FileAccess]::Write,
      [IO.FileShare]::None
    )
    try {
      $SourceStream.CopyTo($DestinationStream)
    } finally {
      $DestinationStream.Dispose()
    }
  } finally {
    $SourceStream.Dispose()
  }
  Protect-CutoverPath $DestinationPath "File"
}

$BootstrapTerraformDataDir = Join-Path $CutoverDir "terraform-data-bootstrap"
$RootTerraformDataDir = Join-Path $CutoverDir "terraform-data-root"
foreach ($TerraformDataDir in @($BootstrapTerraformDataDir, $RootTerraformDataDir)) {
  New-Item -ItemType Directory -Path $TerraformDataDir -ErrorAction Stop | Out-Null
  Protect-CutoverPath $TerraformDataDir "Directory"
}

function Invoke-ProtectedCli {
  param(
    [Parameter(Mandatory = $true)][ValidateSet("aws", "gh")][string]$Command,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z0-9-]+$")][string]$Label,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments
  )
  $InvocationId = New-CutoverArtifactId
  $StdoutPath = Join-Path $CutoverDir "$Label-$InvocationId.stdout.log"
  $StderrPath = Join-Path $CutoverDir "$Label-$InvocationId.stderr.log"
  Assert-CutoverArtifactPathsUnused @($StdoutPath, $StderrPath)
  $NativeErrorPreferenceWasSet = Test-Path Variable:PSNativeCommandUseErrorActionPreference
  $OriginalNativeErrorPreference = if ($NativeErrorPreferenceWasSet) {
    $PSNativeCommandUseErrorActionPreference
  } else {
    $null
  }
  try {
    if ($NativeErrorPreferenceWasSet) {
      $PSNativeCommandUseErrorActionPreference = $false
    }
    & $Command @Arguments 1> $StdoutPath 2> $StderrPath
    $ExitCode = $LASTEXITCODE
  } finally {
    if ($NativeErrorPreferenceWasSet) {
      $PSNativeCommandUseErrorActionPreference = $OriginalNativeErrorPreference
    }
  }
  Protect-CutoverFileIfPresent $StdoutPath
  Protect-CutoverFileIfPresent $StderrPath
  $Stdout = if (Test-Path -LiteralPath $StdoutPath -PathType Leaf) {
    Get-Content -LiteralPath $StdoutPath -Raw
  } else {
    ""
  }
  return [pscustomobject]@{
    ExitCode = $ExitCode
    Stdout = $Stdout
    StdoutPath = $StdoutPath
    StderrPath = $StderrPath
  }
}

function Assert-NoUntrackedTerraformConfiguration {
  param(
    [Parameter(Mandatory = $true)][string]$TerraformDirectory,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $UnignoredResult = Invoke-NativeCommand `
    -Command "git" `
    -Arguments @("-C", $TerraformDirectory, "ls-files", "--others", "--exclude-standard", "--", ".")
  if ($UnignoredResult.ExitCode -ne 0) {
    throw "Could not inspect untracked files: $Label"
  }
  $UnignoredPaths = @($UnignoredResult.Output)
  $IgnoredResult = Invoke-NativeCommand `
    -Command "git" `
    -Arguments @("-C", $TerraformDirectory, "ls-files", "--others", "--ignored", "--exclude-standard", "--", ".")
  if ($IgnoredResult.ExitCode -ne 0) {
    throw "Could not inspect ignored files: $Label"
  }
  $IgnoredPaths = @($IgnoredResult.Output)
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
$MainBranchResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("branch", "--show-current")
if (
  $MainBranchResult.ExitCode -ne 0 -or
  (@($MainBranchResult.Output) | Out-String).Trim() -cne "main"
) {
  throw "MainRepoRoot must be on main."
}
$MainFetchResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("fetch", "origin", "--prune")
if ($MainFetchResult.ExitCode -ne 0) {
  throw "Could not refresh origin refs."
}
$MainShaResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("rev-parse", "origin/main")
$MainSha = (@($MainShaResult.Output) | Out-String).Trim()
if ($MainShaResult.ExitCode -ne 0 -or $MainSha -notmatch "^[0-9a-f]{40,64}$") {
  throw "Could not record the origin/main commit."
}
$RequiredCommitResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("merge-base", "--is-ancestor", "5f16ba5", "HEAD")
if ($RequiredCommitResult.ExitCode -ne 0) {
  throw "PR #126 commit is not an ancestor of HEAD."
}
$MainHeadResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("rev-parse", "HEAD")
if (
  $MainHeadResult.ExitCode -ne 0 -or
  (@($MainHeadResult.Output) | Out-String).Trim() -cne $MainSha
) {
  throw "HEAD must equal the already-fetched origin/main."
}
$MainStatusResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("status", "--porcelain", "--untracked-files=no")
if ($MainStatusResult.ExitCode -ne 0 -or @($MainStatusResult.Output).Count -ne 0) {
  throw "MainRepoRoot has tracked changes."
}
Assert-NoUntrackedTerraformConfiguration `
  $RootDir `
  "Main root stack"

function Assert-MainShaUnchanged {
  param([Parameter(Mandatory = $true)][string]$Checkpoint)
  Set-Location -LiteralPath $MainRepoRoot
  $CheckpointFetchResult = Invoke-NativeCommand `
    -Command "git" `
    -Arguments @("fetch", "origin", "--prune")
  if ($CheckpointFetchResult.ExitCode -ne 0) {
    throw "Could not refresh origin refs at checkpoint: $Checkpoint"
  }
  $ObservedMainResult = Invoke-NativeCommand `
    -Command "git" `
    -Arguments @("rev-parse", "origin/main")
  $ObservedHeadResult = Invoke-NativeCommand `
    -Command "git" `
    -Arguments @("rev-parse", "HEAD")
  $ObservedMainSha = (@($ObservedMainResult.Output) | Out-String).Trim()
  $ObservedHeadSha = (@($ObservedHeadResult.Output) | Out-String).Trim()
  if (
    $ObservedMainResult.ExitCode -ne 0 -or
    $ObservedHeadResult.ExitCode -ne 0 -or
    $ObservedMainSha -cne $MainSha -or
    $ObservedHeadSha -cne $MainSha
  ) {
    throw "The recorded main commit changed at checkpoint: $Checkpoint. Stop before the next update or roll back completed updates."
  }
  $CheckpointStatusResult = Invoke-NativeCommand `
    -Command "git" `
    -Arguments @("status", "--porcelain", "--untracked-files=no")
  if (
    $CheckpointStatusResult.ExitCode -ne 0 -or
    @($CheckpointStatusResult.Output).Count -ne 0
  ) {
    throw "MainRepoRoot gained tracked changes at checkpoint: $Checkpoint"
  }
  Assert-NoUntrackedTerraformConfiguration `
    $RootDir `
    "Main root stack at checkpoint: $Checkpoint"
}

$GhRepositoryResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-repository-view" `
  -Arguments @("repo", "view", "--json", "nameWithOwner")
if ($GhRepositoryResult.ExitCode -ne 0) {
  throw "Could not resolve the GitHub repository. Review protected diagnostic file: $($GhRepositoryResult.StderrPath)"
}
$GhRepositoryJson = $GhRepositoryResult.Stdout
$GhRepository = ConvertFrom-Json -InputObject $GhRepositoryJson
if ([string]$GhRepository.nameWithOwner -cne $Repository) {
  throw "gh is targeting a different repository."
}

$BootstrapState = Join-Path $BootstrapDir "terraform.tfstate"
if (-not (Test-Path -LiteralPath $BootstrapState -PathType Leaf)) {
  throw "Authoritative bootstrap local state was not found. Do not apply from empty state."
}
$BootstrapRepoRootResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapDir, "rev-parse", "--show-toplevel")
$BootstrapRepoRoot = (@($BootstrapRepoRootResult.Output) | Out-String).Trim()
if (
  $BootstrapRepoRootResult.ExitCode -ne 0 -or
  [string]::IsNullOrWhiteSpace($BootstrapRepoRoot)
) {
  throw "Could not resolve the authoritative bootstrap repository."
}
$BootstrapFetchResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapRepoRoot, "fetch", "origin", "--prune")
if ($BootstrapFetchResult.ExitCode -ne 0) {
  throw "Could not refresh bootstrap origin refs."
}
$BootstrapMainResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapRepoRoot, "rev-parse", "origin/main")
$BootstrapMainSha = (@($BootstrapMainResult.Output) | Out-String).Trim()
if (
  $BootstrapMainResult.ExitCode -ne 0 -or
  $BootstrapMainSha -cne $MainSha
) {
  throw "Bootstrap origin/main does not match the recorded main commit."
}
$BootstrapWorkingDiffResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapRepoRoot, "diff", "--quiet", "HEAD", "--", "deploy/aws-ecs/bootstrap")
if ($BootstrapWorkingDiffResult.ExitCode -ne 0) {
  throw "Bootstrap configuration has uncommitted changes."
}
$BootstrapCachedDiffResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapRepoRoot, "diff", "--cached", "--quiet", "HEAD", "--", "deploy/aws-ecs/bootstrap")
if ($BootstrapCachedDiffResult.ExitCode -ne 0) {
  throw "Bootstrap configuration has staged changes."
}
Assert-NoUntrackedTerraformConfiguration $BootstrapDir "BootstrapDir"
$BootstrapBaselineDiffResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapRepoRoot, "diff", "--quiet", $MainSha, "--", "deploy/aws-ecs/bootstrap", ":!deploy/aws-ecs/bootstrap/variables.tf")
if ($BootstrapBaselineDiffResult.ExitCode -ne 0) {
  throw "Bootstrap configuration differs from the recorded main commit outside variables.tf."
}
$VariablesDiffResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @("-C", $BootstrapRepoRoot, "diff", "--unified=0", $MainSha, "--", "deploy/aws-ecs/bootstrap/variables.tf")
if ($VariablesDiffResult.ExitCode -ne 0) {
  throw "Could not compare bootstrap variables.tf with the recorded main commit."
}
$VariablesDiff = @($VariablesDiffResult.Output)
$VariableChangeLines = @(
  $VariablesDiff |
    Where-Object { $_ -match "^[+-]" -and $_ -notmatch "^[+-]{3}" }
)
# `git diff $MainSha` treats the recorded main commit as before and the
# BootstrapDir worktree as after. No change is valid when both contain main.
$ExpectedVariableChangeLines = @(
  '-  default     = "main"',
  '+  default     = "deploy/AWS_ECS"'
)
$UnexpectedVariableChangeLines = @(
  Compare-Object -CaseSensitive $ExpectedVariableChangeLines $VariableChangeLines
)
if (
  $VariableChangeLines.Count -ne 0 -and
  ($VariableChangeLines.Count -ne 2 -or $UnexpectedVariableChangeLines.Count -ne 0)
) {
  throw "Bootstrap variables.tf differs from the recorded main commit in an unexpected way."
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

function Set-ValidatedAwsProfileContext {
  param(
    [Parameter(Mandatory = $true)][string]$SelectedProfile,
    [AllowNull()][AllowEmptyString()][string]$ExpectedAccount = $null
  )
  if ([string]::IsNullOrWhiteSpace($env:AWS_DEMO_ALLOWED_ACCOUNT_IDS)) {
    throw "AWS_DEMO_ALLOWED_ACCOUNT_IDS must already be present in the operator environment."
  }
  $ValidatedAllowedAccounts = @(
    $env:AWS_DEMO_ALLOWED_ACCOUNT_IDS.Split(",") |
      ForEach-Object { $_.Trim() } |
      Where-Object { $_ -match "^[0-9]{12}$" }
  )
  if ($ValidatedAllowedAccounts.Count -eq 0) {
    throw "AWS_DEMO_ALLOWED_ACCOUNT_IDS contains no valid account entries."
  }
  $SelectedCallerResult = Invoke-ProtectedCli `
    -Command "aws" `
    -Label "aws-profile-caller-identity" `
    -Arguments @(
      "sts", "get-caller-identity",
      "--profile", $SelectedProfile,
      "--query", "Account",
      "--output", "text",
      "--no-cli-pager"
    )
  $SelectedCallerAccount = $SelectedCallerResult.Stdout.Trim()
  if (
    $SelectedCallerResult.ExitCode -ne 0 -or
    $SelectedCallerAccount -notmatch "^[0-9]{12}$"
  ) {
    throw "Could not validate the explicitly selected profile caller account. Review protected diagnostic file: $($SelectedCallerResult.StderrPath)"
  }
  if ($ValidatedAllowedAccounts -notcontains $SelectedCallerAccount) {
    throw "The explicitly selected profile caller account is not allowlisted."
  }
  if (
    -not [string]::IsNullOrEmpty($ExpectedAccount) -and
    (
      $ExpectedAccount -notmatch "^[0-9]{12}$" -or
      $SelectedCallerAccount -cne $ExpectedAccount
    )
  ) {
    throw "The selected profile caller account does not match the recorded cutover account."
  }

  $env:AWS_PROFILE = $SelectedProfile
  $EnvironmentCallerResult = Invoke-ProtectedCli `
    -Command "aws" `
    -Label "aws-terraform-environment-caller-identity" `
    -Arguments @(
      "sts", "get-caller-identity",
      "--query", "Account",
      "--output", "text",
      "--no-cli-pager"
    )
  $EnvironmentCallerAccount = $EnvironmentCallerResult.Stdout.Trim()
  if (
    $EnvironmentCallerResult.ExitCode -ne 0 -or
    $EnvironmentCallerAccount -notmatch "^[0-9]{12}$" -or
    $ValidatedAllowedAccounts -notcontains $EnvironmentCallerAccount -or
    $EnvironmentCallerAccount -cne $SelectedCallerAccount
  ) {
    throw "The process environment does not resolve to the allowlisted selected profile account. Review protected diagnostic file: $($EnvironmentCallerResult.StderrPath)"
  }
  return [pscustomobject]@{
    AllowedAccounts = $ValidatedAllowedAccounts
    CallerAccount = $SelectedCallerAccount
  }
}

try {
  $AwsAccountContext = Set-ValidatedAwsProfileContext `
    -SelectedProfile $AwsProfile
  $AllowedAccounts = @($AwsAccountContext.AllowedAccounts)
  $ProfileCallerAccount = [string]$AwsAccountContext.CallerAccount
  Remove-Variable AwsAccountContext
} catch {
  Restore-OriginalAwsProfile
  throw
}

$LastTerraformExitCode = $null
function Invoke-Terraform {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [AllowEmptyCollection()]
    [string[]]$TerraformArguments
  )
  if ([string]$env:AWS_PROFILE -cne $AwsProfile) {
    throw "AWS_PROFILE changed after preflight; refusing to run Terraform."
  }
  $ChdirArguments = @($TerraformArguments | Where-Object { $_ -like "-chdir=*" })
  if ($ChdirArguments.Count -ne 1) {
    throw "Every Terraform invocation must have exactly one -chdir argument."
  }
  $TerraformDirectory = (
    Resolve-Path -LiteralPath $ChdirArguments[0].Substring("-chdir=".Length)
  ).Path
  $PathComparison = if ($IsWindowsPlatform) {
    [StringComparison]::OrdinalIgnoreCase
  } else {
    [StringComparison]::Ordinal
  }
  $TerraformDataDir = if (
    [string]::Equals($TerraformDirectory, $BootstrapDir, $PathComparison)
  ) {
    $BootstrapTerraformDataDir
  } elseif ([string]::Equals($TerraformDirectory, $RootDir, $PathComparison)) {
    $RootTerraformDataDir
  } else {
    throw "Terraform directory is outside the bootstrap/root allowlist."
  }
  $OriginalTerraformDataDirWasSet = Test-Path Env:TF_DATA_DIR
  $OriginalTerraformDataDir = if ($OriginalTerraformDataDirWasSet) {
    [string]$env:TF_DATA_DIR
  } else {
    $null
  }
  $NativeErrorPreferenceWasSet = Test-Path Variable:PSNativeCommandUseErrorActionPreference
  $OriginalNativeErrorPreference = if ($NativeErrorPreferenceWasSet) {
    $PSNativeCommandUseErrorActionPreference
  } else {
    $null
  }
  $script:LastTerraformExitCode = $null
  try {
    $env:TF_DATA_DIR = $TerraformDataDir
    if ($NativeErrorPreferenceWasSet) {
      $PSNativeCommandUseErrorActionPreference = $false
    }
    & terraform @TerraformArguments
    $script:LastTerraformExitCode = $LASTEXITCODE
  } finally {
    if ($NativeErrorPreferenceWasSet) {
      $PSNativeCommandUseErrorActionPreference = $OriginalNativeErrorPreference
    }
    if ($OriginalTerraformDataDirWasSet) {
      $env:TF_DATA_DIR = $OriginalTerraformDataDir
    } else {
      Remove-Item Env:TF_DATA_DIR -ErrorAction SilentlyContinue
    }
  }
}

Write-Host "Selected profile and Terraform environment caller allowlist: OK"

$BootstrapStateBackup = Join-Path $CutoverDir "bootstrap.terraform.tfstate.before"
Copy-ProtectedCutoverFileOnce $BootstrapState $BootstrapStateBackup

$BootstrapVarArgs = @()
if ($BootstrapTfvars) {
  $BootstrapVarArgs += "-var-file=$BootstrapTfvars"
}
Invoke-Terraform "-chdir=$BootstrapDir" init -input=false
if ($LastTerraformExitCode -ne 0) {
  throw "Bootstrap terraform init failed. No apply was attempted."
}

$BackendJson = (
  Invoke-Terraform "-chdir=$BootstrapDir" output -json backend_config | Out-String
)
if ($LastTerraformExitCode -ne 0) {
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

Invoke-Terraform "-chdir=$RootDir" init -input=false -reconfigure `
  "-backend-config=bucket=$($Backend.bucket)" `
  "-backend-config=key=$($Backend.key)" `
  "-backend-config=region=$($Backend.region)" `
  "-backend-config=dynamodb_table=$($Backend.dynamodb_table)" `
  "-backend-config=encrypt=true"
if ($LastTerraformExitCode -ne 0) {
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
  if (
    $LastTerraformExitCode -ne 0 -or
    $RoleArn -notmatch "^arn:[a-z0-9-]+:iam::([0-9]{12}):role/(?:[^/]+/)*([^/]+)$"
  ) {
    throw "Could not validate the role ARN from Terraform output: $OutputName"
  }
  $RoleAccount = $Matches[1]
  $RoleName = $Matches[2]
  if ($RoleAccount -cne $ProfileCallerAccount) {
    Remove-Variable RoleAccount, RoleArn
    throw "The Terraform state account and selected profile account do not match."
  }
  Remove-Variable RoleAccount, RoleArn
  return $RoleName
}

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
  Write-ProtectedCutoverTextOnce `
    $DestinationPath `
    ($Policy | ConvertTo-Json -Depth 20 -Compress)
}

function Backup-RoleTrustAndGetBranch {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$RoleName,
    [switch]$InitialStateBackup
  )
  $RoleResult = Invoke-ProtectedCli `
    -Command "aws" `
    -Label "aws-get-role-$Label" `
    -Arguments @(
      "iam", "get-role",
      "--profile", $AwsProfile,
      "--role-name", $RoleName,
      "--output", "json",
      "--no-cli-pager"
    )
  if ($RoleResult.ExitCode -ne 0) {
    throw "Could not read role trust: $Label. Review protected diagnostic file: $($RoleResult.StderrPath)"
  }
  $RoleJson = $RoleResult.Stdout
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
  $BackupName = if ($InitialStateBackup) {
    "$Label.before.json"
  } else {
    "$Label-$(New-CutoverArtifactId).observed.json"
  }
  $BackupPath = Join-Path $CutoverDir $BackupName
  Write-ProtectedCutoverTextOnce `
    $BackupPath `
    ($Policy | ConvertTo-Json -Depth 20 -Compress)
  return $Branch
}

function Assert-LiveRoleTrustMatchesRecordedPolicy {
  param(
    [Parameter(Mandatory = $true)][string]$RoleName,
    [Parameter(Mandatory = $true)][string]$RecordedPolicyPath,
    [Parameter(Mandatory = $true)][string]$RecordedBranch,
    [Parameter(Mandatory = $true)][string[]]$AllowedLiveBranches,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9-]+$")]
    [string]$ArtifactLabel
  )
  if (-not (Test-Path -LiteralPath $RecordedPolicyPath -PathType Leaf)) {
    throw "The recorded trust policy was not found."
  }
  $CandidateBranches = @($AllowedLiveBranches | Sort-Object -Unique)
  if (
    $RecordedBranch -notin @($OldBranch, $NewBranch) -or
    $CandidateBranches.Count -eq 0 -or
    @($CandidateBranches | Where-Object {
      [string]$_ -notin @($OldBranch, $NewBranch)
    }).Count -ne 0
  ) {
    throw "The live trust comparison branch set is invalid."
  }
  $LiveRoleResult = Invoke-ProtectedCli `
    -Command "aws" `
    -Label "aws-$ArtifactLabel-live-trust" `
    -Arguments @(
      "iam", "get-role",
      "--profile", $AwsProfile,
      "--role-name", $RoleName,
      "--output", "json",
      "--no-cli-pager"
    )
  if ($LiveRoleResult.ExitCode -ne 0) {
    throw "Could not read live trust immediately before replacement. Review protected diagnostic file: $($LiveRoleResult.StderrPath)"
  }
  try {
    $RecordedPolicy = Get-Content -LiteralPath $RecordedPolicyPath -Raw |
      ConvertFrom-Json
    $LivePolicy = (
      ConvertFrom-Json -InputObject $LiveRoleResult.Stdout
    ).Role.AssumeRolePolicyDocument
    Assert-TrustPolicySubjectOnlyChange `
      $RecordedPolicy `
      $RecordedPolicy `
      $RecordedBranch `
      "recorded live-trust baseline"
  } catch {
    throw "Could not validate the recorded/live trust policy; policy values were suppressed."
  }

  $MatchingBranches = @(
    foreach ($CandidateBranch in $CandidateBranches) {
      try {
        Assert-TrustPolicySubjectOnlyChange `
          $RecordedPolicy `
          $LivePolicy `
          $CandidateBranch `
          "live trust immediately before replacement"
        $CandidateBranch
      } catch {
        continue
      }
    }
  )
  if ($MatchingBranches.Count -ne 1) {
    throw "Live trust changed after the recorded backup; stop for human review before replacement."
  }
  return [string]$MatchingBranches[0]
}

function Restore-RoleTrust {
  param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9-]+$")]
    [string]$ArtifactLabel,
    [Parameter(Mandatory = $true)][string]$RoleName,
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$ExpectedBranch
  )
  if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw "Trust backup not found."
  }
  $LiveBranch = Assert-LiveRoleTrustMatchesRecordedPolicy `
    -RoleName $RoleName `
    -RecordedPolicyPath $BackupPath `
    -RecordedBranch $ExpectedBranch `
    -AllowedLiveBranches @($OldBranch, $NewBranch) `
    -ArtifactLabel "$ArtifactLabel-restore-pre-update"
  if ($LiveBranch -ceq $ExpectedBranch) {
    return
  }
  $TrustRestoreResult = Invoke-ProtectedCli `
    -Command "aws" `
    -Label "aws-$ArtifactLabel-restore-update-role" `
    -Arguments @(
      "iam", "update-assume-role-policy",
      "--profile", $AwsProfile,
      "--role-name", $RoleName,
      "--policy-document", "file://$BackupPath",
      "--no-cli-pager"
    )
  if ($TrustRestoreResult.ExitCode -ne 0) {
    throw "Trust restore failed. Review protected diagnostic file: $($TrustRestoreResult.StderrPath)"
  }
}

function Assert-RollbackContext {
  $RequiredRollbackVariables = @(
    "AllowedAccounts",
    "AwsProfile",
    "BeforeBranches",
    "BootstrapDir",
    "BootstrapTerraformDataDir",
    "BootstrapVarArgs",
    "CurrentDeployBranch",
    "CutoverDir",
    "DeployRoleName",
    "IsWindowsPlatform",
    "LifecycleRoleName",
    "MainRepoRoot",
    "NewBranch",
    "OidcAudienceKey",
    "OidcSubjectKey",
    "OldBranch",
    "OriginalAwsProfile",
    "OriginalAwsProfileWasSet",
    "PlanRoleName",
    "ProfileCallerAccount",
    "Repository",
    "RootDir",
    "RootTerraformDataDir",
    "RootTfvars",
    "SmokeRoleName"
  )
  $RollbackContext = @{}
  $MissingVariables = @(
    foreach ($VariableName in $RequiredRollbackVariables) {
      $Variable = Get-Variable `
        -Name $VariableName `
        -Scope 1 `
        -ErrorAction SilentlyContinue
      if ($null -eq $Variable) {
        $VariableName
      } else {
        $RollbackContext[$VariableName] = $Variable.Value
      }
    }
  )
  if ($MissingVariables.Count -ne 0) {
    throw "Rollback context is incomplete; missing variable names: $($MissingVariables -join ', ')."
  }
  if (
    [bool]$RollbackContext["IsWindowsPlatform"] -and
    $null -eq (
      Get-Variable `
        -Name "CurrentWindowsIdentity" `
        -Scope 1 `
        -ErrorAction SilentlyContinue
    )
  ) {
    throw "Rollback context is incomplete; the Windows identity is unavailable."
  }

  $RequiredNonEmptyStrings = @(
    "AwsProfile",
    "BootstrapDir",
    "BootstrapTerraformDataDir",
    "CutoverDir",
    "DeployRoleName",
    "LifecycleRoleName",
    "MainRepoRoot",
    "NewBranch",
    "OidcAudienceKey",
    "OidcSubjectKey",
    "OldBranch",
    "PlanRoleName",
    "ProfileCallerAccount",
    "Repository",
    "RootDir",
    "RootTerraformDataDir",
    "RootTfvars",
    "SmokeRoleName"
  )
  if (
    @($RequiredNonEmptyStrings | Where-Object {
      [string]::IsNullOrWhiteSpace([string]$RollbackContext[$_])
    }).Count -ne 0
  ) {
    throw "Rollback context contains an empty required value; values were suppressed."
  }
  $RoleNamePattern = "^[A-Za-z0-9+=,.@_-]{1,64}$"
  if (
    @("DeployRoleName", "LifecycleRoleName", "PlanRoleName", "SmokeRoleName" |
      Where-Object { [string]$RollbackContext[$_] -notmatch $RoleNamePattern }
    ).Count -ne 0
  ) {
    throw "Rollback context contains an invalid role identity; values were suppressed."
  }
  $BeforeBranchContext = $RollbackContext["BeforeBranches"]
  $RequiredRoleBranchKeys = @(
    "terraform_plan",
    "terraform_lifecycle",
    "github_deploy",
    "oidc_smoke"
  )
  if (
    @($RequiredRoleBranchKeys | Where-Object {
      [string]$BeforeBranchContext[$_] -notin @(
        [string]$RollbackContext["OldBranch"],
        [string]$RollbackContext["NewBranch"]
      )
    }).Count -ne 0 -or
    [string]$RollbackContext["CurrentDeployBranch"] -notin @(
      [string]$RollbackContext["OldBranch"],
      [string]$RollbackContext["NewBranch"]
    )
  ) {
    throw "Rollback context contains an invalid recorded branch; values were suppressed."
  }
  $RollbackAllowedAccounts = @($RollbackContext["AllowedAccounts"])
  if (
    [string]$RollbackContext["ProfileCallerAccount"] -notmatch "^[0-9]{12}$" -or
    $RollbackAllowedAccounts -notcontains [string]$RollbackContext["ProfileCallerAccount"] -or
    [string]$env:AWS_PROFILE -cne [string]$RollbackContext["AwsProfile"]
  ) {
    throw "Rollback AWS account context is not the validated selected profile context."
  }
  if (
    $RollbackContext["OriginalAwsProfileWasSet"] -isnot [bool] -or
    (
      -not [bool]$RollbackContext["OriginalAwsProfileWasSet"] -and
      $null -ne $RollbackContext["OriginalAwsProfile"]
    )
  ) {
    throw "Rollback original AWS_PROFILE state is invalid; values were suppressed."
  }
  foreach ($DirectoryVariable in @(
    "BootstrapDir",
    "BootstrapTerraformDataDir",
    "CutoverDir",
    "MainRepoRoot",
    "RootDir",
    "RootTerraformDataDir"
  )) {
    if (-not (
      Test-Path `
        -LiteralPath ([string]$RollbackContext[$DirectoryVariable]) `
        -PathType Container
    )) {
      throw "Rollback context refers to a missing required directory."
    }
  }
  if (-not (
    Test-Path `
      -LiteralPath ([string]$RollbackContext["RootTfvars"]) `
      -PathType Leaf
  )) {
    throw "Rollback context refers to a missing root tfvars file."
  }

  $RequiredRollbackFunctions = @(
    "Assert-CutoverArtifactPathsUnused",
    "Assert-LiveRoleTrustMatchesRecordedPolicy",
    "Assert-TerraformPlanCallerAccount",
    "Assert-TrustOnlyPlanChanges",
    "Assert-TrustPolicySubjectOnlyChange",
    "Backup-RoleTrustAndGetBranch",
    "ConvertTo-NormalizedJson",
    "ConvertTo-NormalizedJsonValue",
    "Get-JsonDifferencePaths",
    "Get-OidcConditionValues",
    "Get-RequiredOidcSubjectProperty",
    "Get-TerraformPlanResources",
    "Invoke-NativeCommand",
    "Invoke-ProtectedCli",
    "Invoke-Terraform",
    "New-CutoverArtifactId",
    "Protect-CutoverFileIfPresent",
    "Protect-CutoverPath",
    "Restore-OriginalAwsProfile",
    "Restore-RoleTrust",
    "Write-ProtectedCutoverTextOnce"
  )
  $MissingFunctions = @(
    $RequiredRollbackFunctions |
      Where-Object {
        $null -eq (
          Get-Command `
            -Name $_ `
            -CommandType Function `
            -ErrorAction SilentlyContinue
        )
      }
  )
  if ($MissingFunctions.Count -ne 0) {
    throw "Rollback context is incomplete; required shared functions are unavailable."
  }
  foreach ($CommandName in @("aws", "gh", "terraform")) {
    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
      throw "Rollback context is incomplete; a required command is unavailable."
    }
  }
}

$BeforeBranches = [ordered]@{
  terraform_plan = Backup-RoleTrustAndGetBranch "terraform-plan" $PlanRoleName -InitialStateBackup
  terraform_lifecycle = Backup-RoleTrustAndGetBranch "terraform-lifecycle" $LifecycleRoleName -InitialStateBackup
  github_deploy = Backup-RoleTrustAndGetBranch "github-deploy" $DeployRoleName -InitialStateBackup
  oidc_smoke = Backup-RoleTrustAndGetBranch "oidc-smoke" $SmokeRoleName -InitialStateBackup
}
$BeforeBranches.GetEnumerator() | ForEach-Object {
  Write-Host ("{0}: {1}" -f $_.Key, $_.Value)
}

function Get-DeployBranchVariable {
  param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9-]+$")]
    [string]$ArtifactLabel
  )
  $VariablesResult = Invoke-ProtectedCli `
    -Command "gh" `
    -Label $ArtifactLabel `
    -Arguments @("variable", "list", "--json", "name,value")
  if ($VariablesResult.ExitCode -ne 0) {
    throw "gh variable list failed. Review protected diagnostic file: $($VariablesResult.StderrPath)"
  }
  $VariablesJson = $VariablesResult.Stdout
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

$CurrentDeployBranch = Get-DeployBranchVariable "gh-variable-list-initial"
Write-Host "DEPLOY_BRANCH: $CurrentDeployBranch"
if ($CurrentDeployBranch -notin @($OldBranch, $NewBranch)) {
  throw "DEPLOY_BRANCH has an unexpected value."
}
$InitialStatePath = Join-Path $CutoverDir "cutover-initial-state.json"
$InitialStateDocument = [ordered]@{
  schema_version = 2
  main_sha = $MainSha
  repository = $Repository
  branches = [ordered]@{
    old = $OldBranch
    new = $NewBranch
  }
  paths = [ordered]@{
    main_repo_root = $MainRepoRoot
    bootstrap_dir = $BootstrapDir
    root_tfvars = $RootTfvars
    bootstrap_tfvars = $BootstrapTfvars
  }
  selected_aws_context = [ordered]@{
    profile = $AwsProfile
    account = $ProfileCallerAccount
  }
  aws_profile = [ordered]@{
    was_set = $OriginalAwsProfileWasSet
    value = $OriginalAwsProfile
  }
  deploy_branch = $CurrentDeployBranch
  role_names = [ordered]@{
    terraform_plan = $PlanRoleName
    terraform_lifecycle = $LifecycleRoleName
    github_deploy = $DeployRoleName
    oidc_smoke = $SmokeRoleName
  }
  role_branches = $BeforeBranches
}
Write-ProtectedCutoverTextOnce `
  $InitialStatePath `
  ($InitialStateDocument | ConvertTo-Json -Depth 10 -Compress)
Remove-Variable InitialStateDocument
Assert-RollbackContext

$CutoverWorkflows = @(
  [pscustomobject]@{ File = "aws-demo.yml"; Name = "AWS Demo Lifecycle" }
  [pscustomobject]@{ File = "aws-deploy-app.yml"; Name = "AWS Deploy App" }
  [pscustomobject]@{ File = "aws-deploy-frontend.yml"; Name = "AWS Deploy Frontend" }
  [pscustomobject]@{ File = "aws-infra-plan.yml"; Name = "AWS Infra Plan" }
  [pscustomobject]@{ File = "aws-oidc-smoke.yml"; Name = "AWS OIDC Smoke" }
)
$BlockingRunStatuses = @("in_progress", "queued", "waiting", "requested", "pending")

function Assert-NoBlockingCutoverRuns {
  $BlockingRuns = @(
    foreach ($Workflow in $CutoverWorkflows) {
      foreach ($Branch in @($OldBranch, $NewBranch)) {
        foreach ($Status in $BlockingRunStatuses) {
          $BranchLabel = $Branch.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
          $WorkflowLabel = $Workflow.File -replace "\.yml$", ""
          $StatusLabel = $Status -replace "_", "-"
          $RunListResult = Invoke-ProtectedCli `
            -Command "gh" `
            -Label "gh-preflight-$WorkflowLabel-$BranchLabel-$StatusLabel" `
            -Arguments @(
              "api",
              "--method", "GET",
              "--paginate",
              "--slurp",
              "repos/$Repository/actions/workflows/$($Workflow.File)/runs",
              "-f", "branch=$Branch",
              "-f", "status=$Status",
              "-f", "per_page=100"
            )
          if ($RunListResult.ExitCode -ne 0) {
            throw "Could not inspect active workflow runs. Review protected diagnostic file: $($RunListResult.StderrPath)"
          }
          $Pages = ConvertFrom-Json -InputObject $RunListResult.Stdout
          foreach ($Page in $Pages) {
            $WorkflowRunsProperty = $Page.PSObject.Properties["workflow_runs"]
            if ($null -eq $WorkflowRunsProperty) {
              throw "Workflow run query returned an unexpected result shape; values were suppressed."
            }
            $PageRuns = @(
              if ($null -ne $WorkflowRunsProperty.Value) {
                @($WorkflowRunsProperty.Value)
              }
            )
            foreach ($Run in $PageRuns) {
              if (
                [string]$Run.id -notmatch "^[0-9]+$" -or
                [string]$Run.name -cne $Workflow.Name -or
                [string]$Run.head_branch -cne $Branch -or
                [string]$Run.status -cne $Status
              ) {
                throw "Workflow run query returned an unexpected result shape; values were suppressed."
              }
              [pscustomobject]@{
                RunId = [string]$Run.id
                WorkflowName = [string]$Run.name
                Branch = [string]$Run.head_branch
                Status = [string]$Run.status
              }
            }
          }
        }
      }
    }
  )
  if ($BlockingRuns.Count -ne 0) {
    $BlockingRuns |
      Sort-Object RunId -Unique |
      ForEach-Object {
        Write-Host (
          "run_id={0} workflow={1} branch={2} status={3}" -f
          $_.RunId,
          $_.WorkflowName,
          $_.Branch,
          $_.Status
        )
      }
    throw "Active or waiting cutover-related runs exist. Wait for completion or let a human decide whether to cancel them; this runbook never cancels or reruns runs."
  }
  Write-Host "Active or waiting cutover-related runs on old/new branches: none"
}

Assert-NoBlockingCutoverRuns
```

**期待される結果**

- account IDやARNは表示されず、allowlistが`OK`になる。
- 4 roleはrole名ではなく論理labelとbranchだけが表示される。
- D0の想定どおりなら4 labelが`deploy/AWS_ECS`、`DEPLOY_BRANCH`も`deploy/AWS_ECS`になる。
- 既に一部が`main`なら、その事実を記録し、以後のplanでno-opになることを許容する。
- 開始時の`origin/main` SHAが`$MainSha`に保持され、bootstrap側でもfetch後の`origin/main`が同じcommitであることを確認する。以後のcheckpointではSHA自体を表示せず一致だけを検証する。
- `$env:AWS_PROFILE`はこのPowerShell processと子processだけに設定される。明示profile、同じprocess環境のcredential chain、後続plan JSON内のprovider caller accountを同じallowlistと明示profile accountに照合する。
- bootstrap / root stateのrole ARN accountは、role名を利用する前に明示profile accountと一致する。
- main root stackと別worktreeの`$BootstrapDir`の双方に、ignored fileを含む未追跡`.tf` / `.tf.json` / `override.tf` / `override.tf.json` / `*_override.tf`がない。
- `$BootstrapDir`がlinked worktreeならbootstrap側fetchは共有refを安全に再確認し、別cloneならそのclone自身の`origin/main`を更新する。どちらもfetch後のcommitが開始時の`$MainSha`と一致しなければconfiguration diffへ進まない。
- bootstrap configurationの比較は開始時の`$MainSha`を変更前、`$BootstrapDir`のworktreeを変更後とする。権威ある旧`deploy/AWS_ECS` worktree / cloneでは`variables.tf`の`- main` / `+ deploy/AWS_ECS`だけを許可する。`$BootstrapDir`が`main`と同じ内容なら差分なしとなり、これは正常系として継続する。それ以外の差分は停止する。
- `$CutoverDir`に4 trust backup、bootstrap state backup、`cutover-initial-state.json`、bootstrap/root別のTerraform data directory、AWS CLI / `gh`のstdout・stderr診断logが作られる。開始時の`$MainSha`、repository / path / branch入力、選択profileと検証済みaccount、開始時`AWS_PROFILE`、4 role名とbranch、`DEPLOY_BRANCH`は保護済みinitial-state fileに一度だけ保存される。
- `TF_DATA_DIR`はTerraform command実行中だけbootstrap/root別の保護済みdirectoryを指し、command終了時に直前の値へ戻る。root backend metadataはrepository配下の`.terraform`へ作られない。
- initial-state、開始時4 roleの`*.before.json`、bootstrap state backupは固定名のwrite-onceであり、既存pathへの上書きを拒否する。CLI log、開始後のrole snapshot、saved plan / logは呼び出しごとのartifact IDを持つため、再試行でも既存artifactを上書きしない。失敗時は案内されたpathだけを使ってlocalで確認し、中身をterminalや作業記録へ表示しない。
- Unixでは`$CutoverDir`が`0700`、配下fileが`0600`になる。Windowsでは継承を遮断したACLにより実行userだけがdirectoryとfileへアクセスできる。
- 旧branch / `main`の双方について、5 workflowに実行中・待機中runがない。

**失敗時**

- ここではAWS resource変更はない。原因を解消するまで進まない。
- bootstrap stateがない場合、新しい空stateからapplyしてはいけない。権威あるlocal stateまたは安全なbackupを特定する。
- trustが旧branch / `main`以外、複数subject、別repository、複数statementならscope外である。policyを自動整形せず、RAG-18を停止して別レビューへ送る。
- root backend initが失敗した場合、placeholderを実値へ直接置換してcommitしない。bootstrap outputとlocal権限を確認する。
- active runがある場合は完了または人間が判断したキャンセルを待つ。このrunbookは自動キャンセルやrerunを行わない。
- active run gate後は、手順6のsmoke dispatchまで対象workflowを新たに起動しない。gateは手順1でだけ実行するため、手順6でdispatchするsmoke自身を誤検出しない。
- 手順1で停止し、以後TerraformまたはAWS recoveryを実行しない場合は`Restore-OriginalAwsProfile`を呼び、開始時の`AWS_PROFILE`へ戻す。rollbackが必要な場合は復旧完了まで設定を維持し、rollback末尾で戻す。
- AWS変更後にPowerShell sessionが失われた場合、手順1全体を再実行して新しいbefore backupを作ってはならない。元の`$CutoverDir`を再指定し、`Set-StrictMode`、error preference、手順1のfunction定義だけを読み込み直した後、共有CLI logではなくwrite-onceの`cutover-initial-state.json`から開始状態を復元してロールバックを優先する。元のartifact pathを特定できなければ追加更新を停止する。

rollback snippetが外部contextとして参照する変数は、`$AllowedAccounts`、`$AwsProfile`、`$BeforeBranches`、`$BootstrapDir`、`$BootstrapTerraformDataDir`、`$BootstrapVarArgs`、`$CurrentDeployBranch`、`$CutoverDir`、`$DeployRoleName`、`$IsWindowsPlatform`、`$LifecycleRoleName`、`$MainRepoRoot`、`$NewBranch`、`$OidcAudienceKey`、`$OidcSubjectKey`、`$OldBranch`、`$OriginalAwsProfile`、`$OriginalAwsProfileWasSet`、`$PlanRoleName`、`$ProfileCallerAccount`、`$Repository`、`$RootDir`、`$RootTerraformDataDir`、`$RootTfvars`、`$SmokeRoleName`である。Windowsでは`$CurrentWindowsIdentity`も必要である。本経路と復旧経路は同じ`Assert-RollbackContext`でこの一覧、値のshape、protected directory、検証済みaccount context、共有functionを確認する。

session喪失時は、手順1のfunction定義を読み込み直してから次だけを実行する。選択profile、検証済みaccount、role名、repository / path / branch入力はinitial-stateから復元し、`Set-ValidatedAwsProfileContext`でSTS / allowlist検証を再実行する。値は表示しない。

```powershell
$CutoverDir = "<ORIGINAL_CUTOVER_DIR>"
$CutoverDir = (Resolve-Path -LiteralPath $CutoverDir).Path
$InitialStatePath = Join-Path $CutoverDir "cutover-initial-state.json"
if (-not (Test-Path -LiteralPath $InitialStatePath -PathType Leaf)) {
  throw "Original write-once initial state was not found."
}
try {
  $InitialState = Get-Content -LiteralPath $InitialStatePath -Raw | ConvertFrom-Json
  $RecoveredBeforeBranches = [ordered]@{
    terraform_plan = [string]$InitialState.role_branches.terraform_plan
    terraform_lifecycle = [string]$InitialState.role_branches.terraform_lifecycle
    github_deploy = [string]$InitialState.role_branches.github_deploy
    oidc_smoke = [string]$InitialState.role_branches.oidc_smoke
  }
  $RecoveredRoleNames = [ordered]@{
    terraform_plan = [string]$InitialState.role_names.terraform_plan
    terraform_lifecycle = [string]$InitialState.role_names.terraform_lifecycle
    github_deploy = [string]$InitialState.role_names.github_deploy
    oidc_smoke = [string]$InitialState.role_names.oidc_smoke
  }
} catch {
  throw "Could not parse the protected initial state; values were suppressed."
}
$RoleNamePattern = "^[A-Za-z0-9+=,.@_-]{1,64}$"
if (
  [int]$InitialState.schema_version -ne 2 -or
  [string]$InitialState.main_sha -notmatch "^[0-9a-f]{40,64}$" -or
  [string]$InitialState.repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$" -or
  [string]$InitialState.branches.old -cne "deploy/AWS_ECS" -or
  [string]$InitialState.branches.new -cne "main" -or
  [string]::IsNullOrWhiteSpace([string]$InitialState.paths.main_repo_root) -or
  [string]::IsNullOrWhiteSpace([string]$InitialState.paths.bootstrap_dir) -or
  [string]::IsNullOrWhiteSpace([string]$InitialState.paths.root_tfvars) -or
  [string]::IsNullOrWhiteSpace([string]$InitialState.selected_aws_context.profile) -or
  [string]$InitialState.selected_aws_context.account -notmatch "^[0-9]{12}$" -or
  [string]$InitialState.deploy_branch -notin @(
    [string]$InitialState.branches.old,
    [string]$InitialState.branches.new
  ) -or
  @($RecoveredBeforeBranches.Values | Where-Object {
    [string]$_ -notin @(
      [string]$InitialState.branches.old,
      [string]$InitialState.branches.new
    )
  }).Count -ne 0 -or
  @($RecoveredRoleNames.Values | Where-Object {
    [string]$_ -notmatch $RoleNamePattern
  }).Count -ne 0 -or
  $InitialState.aws_profile.was_set -isnot [bool] -or
  (
    -not [bool]$InitialState.aws_profile.was_set -and
    $null -ne $InitialState.aws_profile.value
  )
) {
  throw "Protected initial state has an unexpected shape; values were suppressed."
}
$RequiredInitialArtifacts = @(
  "bootstrap.terraform.tfstate.before",
  "terraform-plan.before.json",
  "terraform-lifecycle.before.json",
  "github-deploy.before.json",
  "oidc-smoke.before.json"
)
foreach ($ArtifactName in $RequiredInitialArtifacts) {
  if (-not (Test-Path -LiteralPath (Join-Path $CutoverDir $ArtifactName) -PathType Leaf)) {
    throw "A required write-once rollback artifact is missing: $ArtifactName"
  }
}
$MainSha = [string]$InitialState.main_sha
$Repository = [string]$InitialState.repository
$OldBranch = [string]$InitialState.branches.old
$NewBranch = [string]$InitialState.branches.new
$MainRepoRoot = (
  Resolve-Path -LiteralPath ([string]$InitialState.paths.main_repo_root)
).Path
$BootstrapDir = (
  Resolve-Path -LiteralPath ([string]$InitialState.paths.bootstrap_dir)
).Path
$RootDir = (
  Resolve-Path -LiteralPath (Join-Path $MainRepoRoot "deploy/aws-ecs")
).Path
$RootTfvars = (
  Resolve-Path -LiteralPath ([string]$InitialState.paths.root_tfvars)
).Path
$BootstrapTfvars = [string]$InitialState.paths.bootstrap_tfvars
if ($BootstrapTfvars) {
  $BootstrapTfvars = (Resolve-Path -LiteralPath $BootstrapTfvars).Path
}
$BootstrapVarArgs = @()
if ($BootstrapTfvars) {
  $BootstrapVarArgs += "-var-file=$BootstrapTfvars"
}
$CurrentDeployBranch = [string]$InitialState.deploy_branch
$BeforeBranches = $RecoveredBeforeBranches
$PlanRoleName = [string]$RecoveredRoleNames.terraform_plan
$LifecycleRoleName = [string]$RecoveredRoleNames.terraform_lifecycle
$DeployRoleName = [string]$RecoveredRoleNames.github_deploy
$SmokeRoleName = [string]$RecoveredRoleNames.oidc_smoke
$AwsProfile = [string]$InitialState.selected_aws_context.profile
$RecordedCallerAccount = [string]$InitialState.selected_aws_context.account
$OriginalAwsProfileWasSet = [bool]$InitialState.aws_profile.was_set
$OriginalAwsProfile = if ($OriginalAwsProfileWasSet) {
  [string]$InitialState.aws_profile.value
} else {
  $null
}
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
$OidcAudienceKey = "token.actions.githubusercontent.com:aud"
$OidcSubjectKey = "token.actions.githubusercontent.com:sub"
$BootstrapTerraformDataDir = Join-Path $CutoverDir "terraform-data-bootstrap"
$RootTerraformDataDir = Join-Path $CutoverDir "terraform-data-root"
foreach ($TerraformDataDir in @($BootstrapTerraformDataDir, $RootTerraformDataDir)) {
  if (-not (Test-Path -LiteralPath $TerraformDataDir -PathType Container)) {
    throw "A protected Terraform data directory is missing."
  }
}
$LastTerraformExitCode = $null
try {
  $AwsAccountContext = Set-ValidatedAwsProfileContext `
    -SelectedProfile $AwsProfile `
    -ExpectedAccount $RecordedCallerAccount
  $AllowedAccounts = @($AwsAccountContext.AllowedAccounts)
  $ProfileCallerAccount = [string]$AwsAccountContext.CallerAccount
} catch {
  Restore-OriginalAwsProfile
  throw
}
Assert-RollbackContext
Set-Location -LiteralPath $MainRepoRoot
Remove-Variable `
  InitialState,
  RecoveredBeforeBranches,
  RecoveredRoleNames,
  RecordedCallerAccount,
  AwsAccountContext
```

### 手順2: bootstrap plan / lifecycle roleを更新

**cwd:** 任意。`terraform -chdir=$BootstrapDir`を使う。

`-target`は通常運用向けではないが、このcutoverではbootstrap stackの他resourceを変更しないための例外的なblast-radius制限として使う。saved planの対象が2 roleのin-place updateだけであることを確認してから、そのplan fileをapplyする。

`Assert-TrustOnlyPlanChanges`は、planの更新前policyをdeep copyし、OIDC subjectの値だけを1箇所置換した期待値を作る。更新後policyはJSON objectのキー順序と空白・改行だけを正規化して完全一致させる。scalarと1要素配列、配列順序は同一shapeのまま比較するため、`Effect`、`Action`、`Principal`（`Federated` providerを含む）、audience、subject以外の全condition、statement数、condition operatorの種類と構成は不変でなければならない。

```powershell
$BootstrapPlanArtifactId = New-CutoverArtifactId
$BootstrapPlan = Join-Path $CutoverDir "bootstrap-main-$BootstrapPlanArtifactId.tfplan"
$BootstrapPlanLog = Join-Path $CutoverDir "bootstrap-main-$BootstrapPlanArtifactId.plan.log"

Assert-CutoverArtifactPathsUnused @($BootstrapPlan, $BootstrapPlanLog)
Invoke-Terraform "-chdir=$BootstrapDir" plan -input=false `
  @BootstrapVarArgs `
  "-var=github_deploy_branch=$NewBranch" `
  "-target=aws_iam_role.terraform_plan" `
  "-target=aws_iam_role.terraform_lifecycle" `
  "-out=$BootstrapPlan" *> $BootstrapPlanLog
$BootstrapPlanExit = $LastTerraformExitCode
Protect-CutoverFileIfPresent $BootstrapPlan
Protect-CutoverFileIfPresent $BootstrapPlanLog
if ($BootstrapPlanExit -ne 0) {
  throw "Bootstrap targeted plan failed. Review the local log without sharing identifiers."
}

$BootstrapPlanJson = (
  Invoke-Terraform "-chdir=$BootstrapDir" show -json $BootstrapPlan | Out-String
)
if ($LastTerraformExitCode -ne 0) {
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
  $BootstrapApplyLog = Join-Path `
    $CutoverDir `
    "bootstrap-main-$BootstrapPlanArtifactId.apply.log"
  Assert-CutoverArtifactPathsUnused @($BootstrapApplyLog)
  Invoke-Terraform "-chdir=$BootstrapDir" apply -input=false $BootstrapPlan *> $BootstrapApplyLog
  $BootstrapApplyExit = $LastTerraformExitCode
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
$RootPlanArtifactId = New-CutoverArtifactId
$RootPlan = Join-Path $CutoverDir "root-deploy-main-$RootPlanArtifactId.tfplan"
$RootPlanLog = Join-Path $CutoverDir "root-deploy-main-$RootPlanArtifactId.plan.log"

Assert-CutoverArtifactPathsUnused @($RootPlan, $RootPlanLog)
Invoke-Terraform "-chdir=$RootDir" plan -input=false `
  "-var-file=$RootTfvars" `
  "-var=github_deploy_branch=$NewBranch" `
  "-target=module.iam.aws_iam_role.github_deploy" `
  "-out=$RootPlan" *> $RootPlanLog
$RootPlanExit = $LastTerraformExitCode
Protect-CutoverFileIfPresent $RootPlan
Protect-CutoverFileIfPresent $RootPlanLog
if ($RootPlanExit -ne 0) {
  throw "Root targeted plan failed. Review the local log without sharing identifiers."
}

$RootPlanJson = (
  Invoke-Terraform "-chdir=$RootDir" show -json $RootPlan | Out-String
)
if ($LastTerraformExitCode -ne 0) {
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
  $RootApplyLog = Join-Path `
    $CutoverDir `
    "root-deploy-main-$RootPlanArtifactId.apply.log"
  Assert-CutoverArtifactPathsUnused @($RootApplyLog)
  Invoke-Terraform "-chdir=$RootDir" apply -input=false $RootPlan *> $RootApplyLog
  $RootApplyExit = $LastTerraformExitCode
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
$SmokeMainPolicy = Join-Path `
  $CutoverDir `
  "oidc-smoke-$(New-CutoverArtifactId).main.json"
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
}

$AllowedSmokeLiveBranches = @($BeforeBranches.oidc_smoke)
if ([string]$BeforeBranches.oidc_smoke -ceq $OldBranch) {
  $AllowedSmokeLiveBranches += $NewBranch
}
$SmokeLiveBranch = Assert-LiveRoleTrustMatchesRecordedPolicy `
  -RoleName $SmokeRoleName `
  -RecordedPolicyPath $SmokeBackup `
  -RecordedBranch $BeforeBranches.oidc_smoke `
  -AllowedLiveBranches $AllowedSmokeLiveBranches `
  -ArtifactLabel "oidc-smoke-pre-update"
if ($SmokeLiveBranch -cne $NewBranch) {
  $SmokeUpdateResult = Invoke-ProtectedCli `
    -Command "aws" `
    -Label "aws-update-oidc-smoke" `
    -Arguments @(
      "iam", "update-assume-role-policy",
      "--profile", $AwsProfile,
      "--role-name", $SmokeRoleName,
      "--policy-document", "file://$SmokeMainPolicy",
      "--no-cli-pager"
    )
  if ($SmokeUpdateResult.ExitCode -ne 0) {
    throw "Smoke trust update failed. Restore the backup policy if verification is not main. Review protected diagnostic file: $($SmokeUpdateResult.StderrPath)"
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
- 置換直前のlive policyがbackupと完全一致する場合だけ更新する。backupが旧branchでliveが既に`main`なら、subjectだけの厳密な旧branch → `main`遷移と一致する場合に限って再試行済みと判定し、更新をskipする。
- branch検証が`main`になる。この時点で`main`からpermissionless smoke roleのAssumeRoleが可能になる。

**失敗時**

- policy shapeまたはsubjectが想定外なら更新しない。
- backup後にlive policyのcondition、provider、Action、statementその他が変わっていれば更新せず、人間の判断まで停止する。
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

$DeployBranchUpdateResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-variable-set-main" `
  -Arguments @("variable", "set", "DEPLOY_BRANCH", "--body", $NewBranch)
if ($DeployBranchUpdateResult.ExitCode -ne 0) {
  throw "DEPLOY_BRANCH update failed. IAM trusts are already main; do not run workflows until this is fixed. Review protected diagnostic file: $($DeployBranchUpdateResult.StderrPath)"
}
$DeployBranchAfter = Get-DeployBranchVariable "gh-variable-list-after-update"
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
$SmokeDispatchResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-smoke-dispatch" `
  -Arguments @("workflow", "run", "aws-oidc-smoke.yml", "--ref", "main")
if ($SmokeDispatchResult.ExitCode -ne 0) {
  throw "OIDC smoke dispatch failed. Review protected diagnostic file: $($SmokeDispatchResult.StderrPath)"
}

$SmokeRun = $null
for ($Attempt = 0; $Attempt -lt 12 -and $null -eq $SmokeRun; $Attempt++) {
  Start-Sleep -Seconds 5
  $SmokeRunsResult = Invoke-ProtectedCli `
    -Command "gh" `
    -Label "gh-smoke-run-list-$Attempt" `
    -Arguments @(
      "run", "list",
      "--workflow", "aws-oidc-smoke.yml",
      "--branch", "main",
      "--event", "workflow_dispatch",
      "--limit", "10",
      "--json", "databaseId,createdAt,headBranch,headSha,status,conclusion"
    )
  if ($SmokeRunsResult.ExitCode -ne 0) {
    throw "Could not list OIDC smoke runs. Review protected diagnostic file: $($SmokeRunsResult.StderrPath)"
  }
  $RunsJson = $SmokeRunsResult.Stdout
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

$SmokeWatchResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-smoke-run-watch" `
  -Arguments @(
    "run", "watch", ([string]$SmokeRun.databaseId), "--exit-status"
  )
if ($SmokeWatchResult.ExitCode -ne 0) {
  throw "OIDC smoke did not complete successfully. Do not dispatch lifecycle workflows. Review protected diagnostic file: $($SmokeWatchResult.StderrPath)"
}
$SmokeViewResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-smoke-run-view" `
  -Arguments @(
    "run", "view", ([string]$SmokeRun.databaseId),
    "--json", "headBranch,headSha,status,conclusion"
  )
$SmokeResultJson = $SmokeViewResult.Stdout
if ($SmokeViewResult.ExitCode -ne 0) {
  throw "Could not inspect the OIDC smoke result. Review protected diagnostic file: $($SmokeViewResult.StderrPath)"
}
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
if ((Get-DeployBranchVariable "gh-variable-list-final") -cne $NewBranch) {
  throw "DEPLOY_BRANCH is not main."
}

Set-Location -LiteralPath $MainRepoRoot
Assert-MainShaUnchanged "before final operational inspection"
$OperationalOldBranchResult = Invoke-NativeCommand `
  -Command "git" `
  -Arguments @(
    "grep", "-n", "deploy/AWS_ECS", "--",
    ".github/workflows/*.yml",
    "deploy/aws-ecs/*.tf",
    ":(glob)deploy/aws-ecs/**/*.tf",
    ":(glob)deploy/aws-ecs/scripts/*.ps1"
  )
if ($OperationalOldBranchResult.ExitCode -notin @(0, 1)) {
  throw "Could not inspect operational files for old-branch dependencies."
}
$OperationalOldBranchMatches = @($OperationalOldBranchResult.Output)
if ($OperationalOldBranchMatches.Count -ne 0) {
  $OperationalOldBranchMatches
  throw "An operational old-branch dependency remains."
}
Write-Host "Operational old-branch references: none"

$BootstrapPostArtifactId = New-CutoverArtifactId
$BootstrapPostPlan = Join-Path `
  $CutoverDir `
  "bootstrap-post-cutover-$BootstrapPostArtifactId.tfplan"
$BootstrapPostLog = Join-Path `
  $CutoverDir `
  "bootstrap-post-cutover-$BootstrapPostArtifactId.plan.log"
Assert-CutoverArtifactPathsUnused @($BootstrapPostPlan, $BootstrapPostLog)
Invoke-Terraform "-chdir=$BootstrapDir" plan -input=false `
  @BootstrapVarArgs `
  "-var=github_deploy_branch=$NewBranch" `
  "-out=$BootstrapPostPlan" `
  -detailed-exitcode *> $BootstrapPostLog
$BootstrapPostExit = $LastTerraformExitCode
Protect-CutoverFileIfPresent $BootstrapPostPlan
Protect-CutoverFileIfPresent $BootstrapPostLog
if ($BootstrapPostExit -notin @(0, 2)) {
  throw "Bootstrap post-cutover full plan failed."
}
$BootstrapPostPlanJson = (
  Invoke-Terraform "-chdir=$BootstrapDir" show -json $BootstrapPostPlan | Out-String
)
if ($LastTerraformExitCode -ne 0) {
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

$RootPostArtifactId = New-CutoverArtifactId
$RootPostPlan = Join-Path `
  $CutoverDir `
  "root-post-cutover-$RootPostArtifactId.tfplan"
$RootPostLog = Join-Path `
  $CutoverDir `
  "root-post-cutover-$RootPostArtifactId.plan.log"
Assert-CutoverArtifactPathsUnused @($RootPostPlan, $RootPostLog)
Invoke-Terraform "-chdir=$RootDir" plan -input=false `
  "-var-file=$RootTfvars" `
  "-var=github_deploy_branch=$NewBranch" `
  "-out=$RootPostPlan" `
  -detailed-exitcode *> $RootPostLog
$RootPostExit = $LastTerraformExitCode
Protect-CutoverFileIfPresent $RootPostPlan
Protect-CutoverFileIfPresent $RootPostLog
if ($RootPostExit -notin @(0, 2)) {
  throw "Root post-cutover full plan failed."
}
$RootPostPlanJson = (
  Invoke-Terraform "-chdir=$RootDir" show -json $RootPostPlan | Out-String
)
if ($LastTerraformExitCode -ne 0) {
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
- `git grep`の「該当なし」exit code `1`は成功として継続し、`0`は検出結果をfail closedする。その他のexit codeは検査失敗として停止する。
- 通常planの`-detailed-exitcode`は、差分なしなら`0`、差分ありなら`2`である。
- native error promotionは終了コードを捕捉するnative commandの実行中だけ無効になり、直後に元の設定へ戻る。したがってplanの`2`はdriftとして検査される一方、想定外exit codeは握り潰されない。
- Terraform管理のtrust roleに差分がある場合は、targeted saved planと同じsubject-only完全比較を通る。

**失敗時**

- full planが`2`でも、この手順ではapplyしない。local logを安全な場所でreviewし、targetingで見落としたtrust関連差分ならD1a内で修正、無関係なdriftなら別issueへ分離する。
- trust roleの差分がsubject-only完全比較に失敗した場合は、providerや追加conditionなどの値を表示せず停止する。
- full planが`1`または`0` / `2`以外ならbackend、tfvars、権限を確認する。trustとsmokeの個別検証が成功済みでも、D1a完了チェックには失敗として記録する。
- 一時ファイルはrollback判断が完了するまで削除しない。

### 手順8: 一時artifactを安全に削除

**cwd:** 任意。手順7とsmokeが成功し、rollbackしないと人間が判断した後だけ実行する。

`$CutoverDir`にはinitial state、state backup、bootstrap/rootのTerraform data directoryとbackend metadata、saved plan、plan log、実trust policy、AWS CLI / `gh`のstdout・stderr診断logが含まれる。長期保管せず、exact pathがOSの一時directory配下かつこのrunbookのprefixであることを検証してから削除する。

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
- repository内のfile、Terraform remote state、AWS resourceは削除されず、repository配下にbackend metadataを残さない。
- process-scoped `AWS_PROFILE`はrunbook開始時の値へ戻り、開始時に未設定なら削除される。
- `TF_DATA_DIR`は各Terraform command直後に元の状態へ戻っており、cleanupでは保護済みdata directoryだけが他artifactとともに削除される。

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

rollbackの基準は旧branchではなく、手順1で記録した開始時状態である。preferred rollbackはTerraform stateを使い、root / bootstrapの各roleをそれぞれの`$BeforeBranches`へ個別に戻す。開始時に`main`だったroleはrollback後も`main`のままであり、旧branchへ強制してはならない。backupを直接適用する経路も、smoke更新と同じ`Assert-LiveRoleTrustMatchesRecordedPolicy`を置換直前に呼び、round 3の厳密比較で現在policyがbackupまたは旧branch / `main`のsubjectだけの遷移と一致しなければ適用前に停止する。既に記録済みbranchへ復元済みなら更新をskipする。

```powershell
Assert-RollbackContext
Set-Location -LiteralPath $MainRepoRoot

if ([string]$env:AWS_PROFILE -cne $AwsProfile) {
  throw "The validated AWS_PROFILE is not active. Restore the runbook session before rollback."
}

$DeployBranchRollbackResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-variable-rollback" `
  -Arguments @(
    "variable", "set", "DEPLOY_BRANCH",
    "--body", $CurrentDeployBranch,
    "--repo", $Repository
  )
$DeployBranchRollbackOk = $DeployBranchRollbackResult.ExitCode -eq 0
if (-not $DeployBranchRollbackOk) {
  Write-Warning "Could not restore DEPLOY_BRANCH. Keep workflows stopped and continue IAM recovery. Review protected diagnostic file: $($DeployBranchRollbackResult.StderrPath)"
}

$SmokeBeforePath = Join-Path $CutoverDir "oidc-smoke.before.json"
Restore-RoleTrust `
  "oidc-smoke-rollback" `
  $SmokeRoleName `
  $SmokeBeforePath `
  $BeforeBranches.oidc_smoke
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
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9-]+$")]
    [string]$ArtifactLabel
  )
  if ($ExpectedBranch -notin @($BeforeBranches.Values)) {
    throw "Rollback branch is not one of the recorded role start values: $ArtifactLabel"
  }
  $RollbackArtifactId = New-CutoverArtifactId
  $RollbackPlan = Join-Path `
    $CutoverDir `
    "$ArtifactLabel-$RollbackArtifactId.before.tfplan"
  $RollbackPlanLog = Join-Path `
    $CutoverDir `
    "$ArtifactLabel-$RollbackArtifactId.before.plan.log"
  Assert-CutoverArtifactPathsUnused @($RollbackPlan, $RollbackPlanLog)
  Invoke-Terraform "-chdir=$TerraformDirectory" plan -input=false `
    @TerraformVarArguments `
    "-var=github_deploy_branch=$ExpectedBranch" `
    "-target=$ResourceAddress" `
    "-out=$RollbackPlan" *> $RollbackPlanLog
  $RollbackPlanExit = $LastTerraformExitCode
  Protect-CutoverFileIfPresent $RollbackPlan
  Protect-CutoverFileIfPresent $RollbackPlanLog
  if ($RollbackPlanExit -ne 0) {
    throw "Recorded-state rollback plan failed: $ArtifactLabel"
  }
  $RollbackPlanJson = (
    Invoke-Terraform "-chdir=$TerraformDirectory" show -json $RollbackPlan | Out-String
  )
  if ($LastTerraformExitCode -ne 0) {
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
    $RollbackApplyLog = Join-Path `
      $CutoverDir `
      "$ArtifactLabel-$RollbackArtifactId.before.apply.log"
    Assert-CutoverArtifactPathsUnused @($RollbackApplyLog)
    Invoke-Terraform "-chdir=$TerraformDirectory" apply -input=false $RollbackPlan *> $RollbackApplyLog
    $RollbackApplyExit = $LastTerraformExitCode
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
Assert-RollbackContext
Set-Location -LiteralPath $MainRepoRoot

if ([string]$env:AWS_PROFILE -cne $AwsProfile) {
  throw "The validated AWS_PROFILE is not active. Restore the runbook session before direct recovery."
}

$DirectDeployBranchRecoveryResult = Invoke-ProtectedCli `
  -Command "gh" `
  -Label "gh-variable-direct-recovery" `
  -Arguments @(
    "variable", "set", "DEPLOY_BRANCH",
    "--body", $CurrentDeployBranch,
    "--repo", $Repository
  )
$DirectDeployBranchRecoveryOk = $DirectDeployBranchRecoveryResult.ExitCode -eq 0
if (-not $DirectDeployBranchRecoveryOk) {
  Write-Warning "Could not restore DEPLOY_BRANCH. Keep workflows stopped and continue direct IAM recovery. Review protected diagnostic file: $($DirectDeployBranchRecoveryResult.StderrPath)"
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
    "$($RecoveryRole.Label)-direct-recovery" `
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
- [ ] bootstrap repositoryをfetchし、その`origin/main`が開始時の`$MainSha`と一致する
- [ ] 旧branch / `main`の5 workflowに`in_progress` / `queued` / `waiting` / `requested` / `pending` runがない
- [ ] 権威あるbootstrap local stateとroot remote stateを特定した
- [ ] bootstrap / root stateのrole ARN accountが明示profile accountと一致する
- [ ] 直近applyと同一のbootstrap/root入力を使用した
- [ ] initial-state、4 role backup、bootstrap state backupが保護済み領域にwrite-onceで保存された
- [ ] bootstrap/rootの`TF_DATA_DIR`が保護済みの別directoryを使い、各command後に元の設定へ戻る
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
- [ ] `git grep`のexit `1`とfull planのexit `2`が期待値として処理され、native error promotionは各command後に復元された
- [ ] saved plan内のTerraform provider caller accountが明示profile accountと一致し、allowlist内である
- [ ] account ID、ARN、state、plan、trust backup、secret、tokenをissue / PR / chatへ貼っていない
- [ ] AWS CLI / `gh`のstdout・stderrは保護済み`$CutoverDir`だけに保存し、terminalへ表示していない
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
