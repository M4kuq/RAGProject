# AWS作業のbase branch決定

- 検証日: 2026-07-27
- D0 issue: [RAG-23](https://keichannel116.atlassian.net/browse/RAG-23)
- 対象: AWS workflow、GitHub OIDC trustのbranch scope、AWS運用文書
- 状態: D0のコード切り替え。AWS側のtrust更新はD1（RAG-22）で実施する

## 決定

Phase4以降のAWS作業は`main`をbaseとする。旧`deploy/AWS_ECS` branchは退役扱いとし、新規作業のbaseにはしない。D0ではbranch自体を削除しない。

承認済みのparity検証結果から、**`deploy/AWS_ECS`にmain未反映の内容はない**。したがって旧branchからコードを取り込む変更は行わず、branch名の付け替えと運用文書の更新だけを行う。

## branch parityの根拠

### 比較対象

| 対象 | commit | root tree |
|---|---|---|
| `origin/deploy/AWS_ECS` | `09f4c095c7d38b741d67c58de82fc3ef288faee5` | `6e4584e83c2cf05da419a65ad53ed957e5c80009` |
| mainへ入ったPR #88 | `42ceb7305a40765fa522f4720bf4dec8084313ff` | `6e4584e83c2cf05da419a65ad53ed957e5c80009` |
| 検証時の`origin/main` | `a360159be1a547cd70457b6b5be38d903a875ff5` | PR #88に加えて後続変更を含む |

`origin/deploy/AWS_ECS`とPR #88 commitのroot tree SHAは一致する。また、両commit間のdiffは空である。これは旧branchの最終内容がPR #88としてmainへ到達したことを示す。

検証時の`origin/main`と比べた93 filesの差分は、すべてmain側の後続変更である。内訳は追加31、更新62、main側削除0であり、旧branch側にしかない変更はない。評価信頼性基盤のPR #121、#122、#123、#124はいずれも検証時の`origin/main`のancestorである。

これらの結果はtask ownerが受理済みであり、D0の追加指示に従って再検証せず本書へ記録した。

### 再現コマンド

固定したcommitを使うことで、後日`origin/main`が進んでも同じ比較を再現できる。

```bash
git rev-parse 'origin/deploy/AWS_ECS^{tree}'
git rev-parse '42ceb7305a40765fa522f4720bf4dec8084313ff^{tree}'
git diff --exit-code origin/deploy/AWS_ECS 42ceb7305a40765fa522f4720bf4dec8084313ff
git diff --name-status origin/deploy/AWS_ECS a360159be1a547cd70457b6b5be38d903a875ff5
```

PR #121〜#124のmerge commitについては、各commitに対して次を実行し、終了コード0であることを確認した。

```bash
git merge-base --is-ancestor <PRのmerge commit> a360159be1a547cd70457b6b5be38d903a875ff5
```

## D0で付け替えるbranch固定

guardは削除せず、`deploy/AWS_ECS`から`main`へ付け替える。

| 対象 | 変更 |
|---|---|
| `aws-demo.yml` | branch guard、checkout ref、競合runのbranch filter、`TF_VAR_github_deploy_branch`、concurrency groupを`main`へ変更 |
| `aws-deploy-app.yml` | branch guard、checkout ref、`source_sha`のancestor判定、concurrency groupを`main`へ変更 |
| `aws-deploy-frontend.yml` | branch guard、checkout ref、`source_sha`のancestor判定、concurrency groupを`main`へ変更 |
| `aws-infra-plan.yml` | 手動plan jobのbranch guardを`main`へ変更 |
| `aws-oidc-smoke.yml` | job guardとcheckout refを`main`へ変更 |
| `scripts/aws-demo.ps1` | fail-closedの`ExpectedBranch`だけを`main`へ変更 |
| root Terraform | `variables.tf`の`github_deploy_branch` defaultは既に`main`であり、変更不要 |
| bootstrap Terraform | `bootstrap/variables.tf`の`github_deploy_branch` defaultを`main`へ変更 |
| OIDC smoke bootstrap | `aws-oidc-bootstrap.ps1`のbranch defaultを`main`へ変更 |

concurrency groupの名前もmain用へ変わるため、旧branchで既に実行中のjobとは相互排他にならない。旧branchを今後の実行元にしないことを前提とする。

## D1のOIDC trustカットオーバー

### 対象は3系統

次のすべてがbranch scopeを持つため、1つでも旧branchのままではmainから対応workflowを実行できない。

1. root moduleのdeploy role
   `deploy/aws-ecs/variables.tf`の`github_deploy_branch`が制御する。
2. bootstrapのTerraform plan roleとTerraform lifecycle role
   `deploy/aws-ecs/bootstrap/variables.tf`の`github_deploy_branch`が制御する。
3. permissionless OIDC smoke role
   `deploy/aws-ecs/scripts/aws-oidc-bootstrap.ps1`のbranchが制御する。

### `aws-demo.ps1`経由で先行applyできない理由

`aws-demo.ps1`は、現在のGit branchが`ExpectedBranch`と一致することに加え、`TF_VAR_github_deploy_branch`も`ExpectedBranch`と一致することを強制する。したがって旧`deploy/AWS_ECS` branch上で`github_deploy_branch = main`を渡すと、Terraform実行前に必ず停止する。

このため「最後に一度、旧branchからscript経由で`github_deploy_branch = main`をapplyする」という手順は採用しない。

### 基本手順

以下はD1（RAG-22）で人間がレビューし、ローカルから直接実行する。D0ではAWS認証、`terraform plan`、`terraform apply`、IAM更新、workflow実行を行わない。

1. AWS lifecycleを停止した状態で、変更対象の3系統と現在のtrust subjectを確認する。識別子や実ARNはPRやログへ記録しない。
2. `deploy/aws-ecs/bootstrap`で`github_deploy_branch = main`を明示し、人間がローカルから直接Terraformをplan/applyしてplan roleとlifecycle roleを更新する。
3. `deploy/aws-ecs`で`github_deploy_branch = main`を明示し、人間がローカルから直接Terraformをplan/applyしてroot moduleのdeploy roleを更新する。
4. 既存のOIDC smoke roleのtrust subjectを、人間がレビューしたIAM更新手順で`refs/heads/main`へ変更する。現行の`aws-oidc-bootstrap.ps1`は既存roleのtrust差分を検出するとfail closedで停止し、上書きしない。D0で変更するdefaultは、新規作成時または更新後の検証期待値を`main`に揃えるためのものである。
5. 3系統すべてのtrust subjectが`repo:<OWNER>/<REPO>:ref:refs/heads/main`だけを許可することを確認する。
6. GitHub repository variable `DEPLOY_BRANCH`を`main`へ変更する。D0では外部設定を変更しない。
7. `main`からOIDC smoke、Terraform plan、lifecycleの順に確認し、以後は`main`だけを実行元にする。

別案として、旧branch上で一時的に`aws-demo.ps1`のguardを外してapplyすることは技術的には可能だが、fail-closedの運用を弱めるため推奨しない。どちらの方法もD1の範囲であり、D0ではguardを削除も緩和もしない。

## D0 merge後からD1完了までの既知の制約

- mainのAWS workflowはbranch guardを通過するが、AWS側のtrustが旧branch向けのためAssumeRoleに失敗する。
- 旧branchのworkflowとscriptファイルは変更しないため、旧branch側は従来どおり旧branchを期待する。ただし旧branchは退役扱いであり、新しい運用経路として使わない。
- GitHub repository variable `DEPLOY_BRANCH`は現行の`deploy/AWS_ECS`のまま残る。D0 merge後はworkflowの期待値`main`と食い違うため、D1で更新するまでTerraform planを含むAWS workflowを実行しない。
- このためD0 merge後からD1完了まで、AWS lifecycleは実質実行できない。これは意図した中間状態であり、D1で3系統のtrustとrepository variableを揃えて解消する。

## scopeと残存文字列

`git grep -n "AWS_ECS"`と`git grep -n "deploy/AWS_ECS"`でtracked filesを確認する。

- workflow、PowerShell実装、対応テスト、Terraform default/example、現行運用記述は`main`へ付け替える。
- 本書のparity根拠、退役方針、カットオーバー説明、およびD1までの現行`DEPLOY_BRANCH`値では、旧branch名を意図して残す。
- 許可scope外に旧branch前提が残る場合は変更せず、PRと最終報告で一覧化する。

## セキュリティと課金

D0ではAWSリソースを作成・更新・削除しない。AWS認証を伴う操作や課金を伴う操作も行わない。文書にはsecret、credential、token、`.env`値、AWS account ID、実ARN、raw document/chunk text、PIIを記録しない。
