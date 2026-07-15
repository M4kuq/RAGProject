# AWS ECS Fargate デプロイ手順

このrunbookは `deploy/AWS_ECS` branchのAWS demo stackと、Bedrock/S3対応済みアプリを配備する手順です。新規アカウントとクレジット運用は [AWS_PAID_PLAN_CREDIT_SETUP.md](./AWS_PAID_PLAN_CREDIT_SETUP.md) を参照してください。CIはGitHub OIDCの短期認証だけを使い、アプリはECS task roleのdefault credential chainを使います。静的AWS access keyは使いません。

## 1. 前提

- AWS account は demo 用に分離することを推奨します。
- Region は `ap-northeast-1` を使います。
- GitHub Actions の deploy 対象 branch は Terraform 変数 `github_deploy_branch` で固定します。PR #88 の branch で動かす場合は `deploy/AWS_ECS` にします。
- AWS Console で Bedrock model access を有効化します。
  - Amazon Nova Lite generation model: `bedrock_generation_model_id`
  - Titan Text Embeddings V2: `amazon.titan-embed-text-v2:0`
  - Bedrock Rerank: `bedrock_rerank_model_id`
- apply前にgeneration modelのlifecycleを確認し、`ACTIVE`でない既定値は現行active modelへ更新します。Nova Liteのmax output 5Kに合わせ、ECSは`GENERATION_MAX_OUTPUT_TOKENS=5000`を設定します。

```bash
aws bedrock get-foundation-model \
  --region ap-northeast-1 \
  --model-identifier amazon.nova-lite-v1:0 \
  --query 'modelDetails.modelLifecycle.status' \
  --output text
```

- `terraform apply` は CI では実行しません。PR CI は認証なしで `terraform fmt`、`terraform init -backend=false`、`terraform validate` まで実行します。AWS 認証と remote state を使う `terraform plan` は `workflow_dispatch` の手動実行だけで扱い、初回 bootstrap と root apply は人手でレビューして実行します。

### Graph backend policy

ECS 版は `graph_store_provider = "postgres"` を既定にし、API/worker task の `GRAPH_STORE_PROVIDER=postgres` で RDS PostgreSQL 上のグラフテーブルを使います。Neo4j コンテナはこの ECS stack に含めず、Neo4j は EKS 本番 HA 版で StatefulSet と永続ボリュームを使う read model / projection として扱う予定です。source of truth は Postgres のままです。

Amazon Neptune はこの demo stack では採用しません。Neptune openCypher は Neo4j Cypher と完全互換ではなく、APOC や CONSTRAINT など Neo4j 前提の機能にはアプリ改修が必要です。また完全な scale-to-zero ができないため、短時間 demo のコスト方針に合いません。将来 managed graph DB を見せる場合は、`NeptuneGraphStore` のような別 provider として検討します。

## 2. Secrets Manager と秘密値

RDS master password は `manage_master_user_password = true` により RDS managed secret になります。Terraform file や GitHub secrets に RDS master password は置きません。

Terraform が ECS task definition に ARN として渡す Secrets Manager secret:

| secret | 用途 | 作成タイミング |
|---|---|---|
| `DATABASE_URL` | API/worker の DB 接続文字列 | root apply 前に secret だけ作成し、RDS 作成後に値を更新 |
| `SESSION_SECRET` | session cookie 署名用の強いランダム値 | root apply 前 |
| `RAG_DEMO_ADMIN_PASSWORD` | AWS demo管理者の16文字以上のpassword。migration taskだけへ注入 | root apply 前 |
| `additional_secret_arns` | 将来の provider token など | 必要になった時だけ |

Terraform sensitive variable または GitHub Actions secret として渡す値:

| 値 | 用途 | GitHub Actions |
|---|---|---|
| `basic_auth_header_sha256` | CloudFront Function の Basic 認証判定。`Authorization` header 全体の SHA-256 hex | `secrets.BASIC_AUTH_HEADER_SHA256` |
| `origin_verify_header_value` | CloudFront から ALB origin への private header 値 | `secrets.ORIGIN_VERIFY_HEADER_VALUE` |

これらのplaintextはcommitしません。Basic認証password、origin verify header value、`.env`、cookie、tokenはPRやworkflow logに出さないでください。アプリ用Secret ARNの読取はECS task execution roleだけに許可し、API/worker containerのtask roleからは`secretsmanager:GetSecretValue`を除外します。GitHub deploy roleの読取は、秘密値を含まないruntime deployment-config secret 1件だけに分離します。

## 3. GitHub Actions variables / secrets

GitHub repository の `Settings > Secrets and variables > Actions` に設定します。

### Variables

| name | 取得元 |
|---|---|
| `AWS_REGION` | `ap-northeast-1` |
| `OIDC_REPO` | `OWNER/REPO`。Terraform 変数 `github_oidc_repo` と同じ値 |
| `DEPLOY_BRANCH` | `deploy/AWS_ECS`。Terraform 変数 `github_deploy_branch` と同じ値 |
| `BASIC_AUTH_USERNAME` | Terraform 変数 `basic_auth_username` と同じ値 |

### Secrets

| name | 内容 |
|---|---|
| `AWS_DEMO_ACCOUNT_ID` | deploy roleを組み立てる12桁account ID。workflow開始直後にmaskする |
| `AWS_DEMO_ALLOWED_ACCOUNT_IDS` | 許可するsandbox account ID。通常は上記1件のみ |
| `AWS_TERRAFORM_LIFECYCLE_ROLE_ARN` | runtime作成・削除用OIDC role ARN |
| `AWS_TERRAFORM_PLAN_ROLE_ARN` | read-only Terraform plan用OIDC role ARN |
| `TF_STATE_BUCKET` / `TF_LOCK_TABLE` / `TF_STATE_KEY` | bootstrapのremote state識別子 |
| `AWS_GITHUB_OIDC_PROVIDER_ARN` | separately managed OIDC provider ARN |
| `DATABASE_URL_SECRET_ARN` | 手作成した `DATABASE_URL` secret ARN |
| `SESSION_SECRET_ARN` | 手作成した `SESSION_SECRET` secret ARN |
| `DEMO_ADMIN_PASSWORD_SECRET_ARN` | 手作成した `RAG_DEMO_ADMIN_PASSWORD` secret ARN |
| `BASIC_AUTH_HEADER_SHA256` | `Basic base64(username:password)` header 文字列の SHA-256 hex |
| `ORIGIN_VERIFY_HEADER_VALUE` | 32 文字以上のランダム値。Terraform 変数 `origin_verify_header_value` と同じ値 |
| `RAG_DEMO_ADMIN_EMAIL` | migrationが作成する管理者email。現在は`admin@example.com`と一致させる |
| `RAG_DEMO_ADMIN_PASSWORD` | `RAG_DEMO_ADMIN_PASSWORD` Secrets Manager secretと同じ値。load-data/smoke login専用 |

`aws-demo.ps1 up` はfresh apply直後のTerraform outputsをruntime secret `ragproject-demo-deployment-config`へ保存します。app/frontend workflowはOIDC認証後にこのsecretを読み、workflow_dispatch inputやrepository variableへECR URL、bucket、distribution、subnet、security groupなどを露出しません。このruntime secretは`down`のTerraform destroy対象です。lifecycle roleには同secretへの`secretsmanager:PutSecretValue`、deploy roleには同secretだけへの`secretsmanager:GetSecretValue`が必要です。

`AWS_TERRAFORM_PLAN_ROLE_ARN` は deploy role と分けます。plan role には remote state の read/lock と、この stack の plan に必要な read-only 権限だけを付けます。deploy role は `terraform apply` 権限を持たず、runtime deployment configの読取、ECR push、ECS deploy、migration run、frontend S3 sync、CloudFront invalidationだけに使います。

`AWS Infra Plan` の PR job は `terraform fmt -check -recursive`、`terraform init -backend=false`、`terraform validate` だけを実行します。この経路では AWS OIDC、remote state backend、GitHub secrets、Terraform sensitive variables を使いません。AWS 認証を伴う `terraform plan` は `workflow_dispatch` の手動 job だけで実行します。Fork PR には remote state や GitHub secrets を渡さず、認証不要の通常 CI と人手レビューで確認します。

## 4. OIDC trust

Terraform module `modules/iam` は GitHub deploy role の trust policy を次の subject に限定します。

```text
repo:<OWNER>/<REPO>:ref:refs/heads/<github_deploy_branch>
```

PR #88 の branch で手動 deploy workflow を動かす場合:

```hcl
github_oidc_repo     = "OWNER/REPO"
github_deploy_branch = "deploy/AWS_ECS"
```

AWS account に `token.actions.githubusercontent.com` provider が既にある場合は、Terraform 変数で次のようにします。

```hcl
create_github_oidc_provider = false
# github_oidc_provider_arn = "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
```

account ID や実 ARN は workflow file に書かず、GitHub secretsまたはTerraform outputから渡します。

### 4.1 最小 OIDC smoke

runtime 用の広い権限を作る前に、GitHub OIDC の成立だけを確認できます。この経路は IAM OIDC provider と権限ポリシーを持たない専用 role だけを対象にし、Terraform、ECS、RDS、ECR、Bedrock は実行しません。`sts:GetCallerIdentity` は権限ポリシーなしで呼び出せるため、この role に AWS service 権限を追加しないでください。

まず `AWS_DEMO_ALLOWED_ACCOUNT_IDS` を安全な環境変数として設定し、read-only の review を実行します。Account ID や ARN を端末ログへ出力しません。

```powershell
./deploy/aws-ecs/scripts/aws-oidc-bootstrap.ps1 plan -Repository "OWNER/REPO"
```

表示内容を確認し、IAM resource 作成が別途明示承認された場合だけ、完全一致の確認語を付けて実行します。

```powershell
./deploy/aws-ecs/scripts/aws-oidc-bootstrap.ps1 apply `
  -Repository "OWNER/REPO" `
  -Confirmation CREATE-GITHUB-OIDC-SMOKE
```

既存 provider の audience または既存 role の trust が期待値と異なる場合、script は上書きせず停止します。新規 provider の thumbprint は AWS IAM に取得させます。作成後は role ARN を表示せずに Actions secret `AWS_OIDC_SMOKE_ROLE_ARN` へ設定し、同じ allowlist を `AWS_DEMO_ALLOWED_ACCOUNT_IDS` へ設定します。これらはAWS credentialではありませんが、repository variableではstep開始時の`env` / `with`表示に値が出るため、GitHubの自動maskを得る目的でsecretとして保存します。

`AWS OIDC Smoke` workflow は default branch に存在してから手動実行し、ref には `deploy/AWS_ECS` を選択します。workflow は15分の短期認証を取得し、account ID を mask して専用 verifier で role session を照合します。これは `AWS_TERRAFORM_PLAN_ROLE_ARN` や `AWS_TERRAFORM_LIFECYCLE_ROLE_ARN` の代替ではありません。

公式情報:

- [GitHub Actions から AWS へ OIDC 接続](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [IAM OIDC provider の作成](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)
- [STS GetCallerIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html)

## 5. State bootstrap

remote state backend は root stack 自身からは作れないため、先に `bootstrap/` を local backend で apply します。

同じbootstrapは、`deploy/AWS_ECS`だけを信頼するread-only Terraform plan roleとTerraform lifecycle role、`DATABASE_URL`、`SESSION_SECRET`、`RAG_DEMO_ADMIN_PASSWORD`の値を持たないSecret containerも管理します。plan roleの書き込み権限はDynamoDB state lock操作だけで、runtime lifecycle権限やSecret値読取権限は持ちません。lifecycle roleはroot stackが使用するサービスの書き込み操作、固定したruntime IAM role/policy、remote state更新、DATABASE_URLとruntime deployment-configへの値書き込み、RDS管理master secretの値読取だけに限定します。

```bash
cd deploy/aws-ecs/bootstrap
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
# 内容を確認してから人手で実行
terraform apply
```

出力を確認します。

```bash
terraform output -raw state_bucket
terraform output -raw lock_table
terraform output -json backend_config
```

その値を root の `backend.tf` に反映するか、`terraform init` 時に `-backend-config` で渡します。GitHub Actions では `TF_STATE_BUCKET`、`TF_LOCK_TABLE`、`TF_STATE_KEY` を使います。

## 6. Root infra apply

`DATABASE_URL`、`SESSION_SECRET`、`RAG_DEMO_ADMIN_PASSWORD`のSecrets Manager ARN、Basic認証hash、origin verify header、GitHub OIDC設定をtfvarsか安全な環境変数注入で渡して実行します。

```bash
cd deploy/aws-ecs
terraform init -reconfigure
terraform fmt -check -recursive
terraform validate
terraform plan
# plan とコストをレビューしてから人手または承認付き手順で実行
terraform apply
```

CI workflow `AWS Infra Plan` は PR では `terraform fmt`、`init -backend=false`、`validate` までです。`terraform plan` は AWS OIDC と secrets が必要なため `workflow_dispatch` 専用 job に分けています。`terraform apply` は workflow に入れません。

初回 root apply 後、RDS managed master secret から password を確認し、手作成済み `DATABASE_URL` secret の値を実 DB endpoint に更新します。secret 値は log や PR に出さないでください。

## 7. 初回アプリデプロイ

1. `AWS Deploy App` workflow を `deploy/AWS_ECS` branch から手動実行します。
2. `image_tag` を空にすると workflow 実行コミット SHA が使われます。任意の tag を指定することもできます。
3. workflow は API image と worker image を `backend/Dockerfile` の `backend` / `worker` target で build し、ECR に push します。
4. workflow は Terraform が作った migration task definition family を base に、新しい API image tag の revision を登録します。
5. migration one-off task で `alembic upgrade head` の後に `APP_ENV=local python -m app.scripts.seed --skip-document-indexing --deployed-admin-from-env` を実行します。管理者passwordは専用のSecrets Manager secretからmigration taskだけへ注入します。既知passwordのlocal viewerと未indexのdemo documentsは作成せず、以前のseedで残っているlocal admin/viewerは無効化してpasswordも無作為化します。停止を待って`migration` containerのexit codeが`0`であることを確認します。
6. migration と seed bootstrap 成功後に API/worker task definition revision を登録し、API/worker service を新 revision に更新します。
7. この workflow は `desired_count` を変更しません。scale-to-zero demo 方針に合わせ、起動が必要な時だけ Terraform 変数または AWS CLI で明示的に増やします。

起動する場合の例:

```bash
terraform apply \
  -var='api_desired_count=1' \
  -var='qdrant_desired_count=1' \
  -var='worker_desired_count=0'
```

`worker_desired_count` はscale-to-zero既定で `0` です。upload/indexingを実行するときだけ `1` へ上げます。

Qdrant は NFS/EFS ではなく、ECS service-managed EBS `gp3` volume を `/qdrant/storage` に mount します。EBS は block storage なので Qdrant の storage 要件に合いますが、ECS service が管理する volume は task replacement や scale-to-zero で削除されます。Qdrant collection は永続的な source of truth ではないため、task 置換、scale-to-zero 後、Bedrock adapter 有効化後は source documents から再indexしてください。

## 8. Task definition とimage tagの整合

`up`はアプリworkflowへ渡したGit SHAと同じ値を、scale-up用の保存済みTerraform planへ`api_image_tag` / `worker_image_tag`として渡します。これにより、workflowが登録したrevisionの後でTerraformがserviceを更新しても、`placeholder`や古いimageへ戻りません。

Terraformはbaseline task definitionを所有し、workflowはmigration/API/workerのdeploy revisionを追加します。`down`はruntime stackのdestroy後に、対象familyに残るACTIVE revisionを列挙してderegisterします。別projectのfamilyは対象にしません。

schema migrationとseed bootstrapは、API/workerのservice更新とscale-upより先に完了させます。

## 9. フロント配信

`AWS Deploy Frontend` workflow を `deploy/AWS_ECS` branch から手動実行します。

フロントエンドは S3 + CloudFront の静的配信です。ECS の frontend container は使いません。

workflow の処理:

1. OIDC で固定名 `ragproject-demo-github-deploy` のroleをassumeし、許可account IDを検証します。
2. `frontend` で `npm ci`、`npm run build` を実行します。
3. `frontend/dist` を `s3://$FRONTEND_BUCKET_NAME` に `aws s3 sync --delete` します。
4. `CLOUDFRONT_DISTRIBUTION_ID` に `create-invalidation --paths "/*"` を実行します。

疎通確認:

```bash
terraform output -raw cloudfront_domain_name
```

表示された CloudFront domain に HTTPS でアクセスし、Basic 認証後に画面と `/ready` 系の API routing を確認します。

## 10. Seed / indexing

schema migration 後の bootstrap seed は deploy workflow の one-off task で実行します。`--deployed-admin-from-env` は管理者、role、システム設定だけを作り、readyなのにQdrantへ未投入となるdemo documentsは作成しません。`--skip-document-indexing` も付け、migration taskからQdrantへ接続しないことを明示します。

ECS envは `GENERATION_PROVIDER=bedrock`、`EMBEDDING_PROVIDER=bedrock`、`RERANK_PROVIDER=bedrock`、`STORAGE_BACKEND=s3` です。モデルIDはTerraform変数から渡し、Titan Text Embeddings V2の1024次元を `document_chunks_bedrock_titan_v2` collectionと一致させます。

Qdrantのservice-managed EBSはtask replacement/scale-to-zeroで削除されるため、S3 source documentsから再indexしてください。初回live smokeはAPI、Qdrant、workerを起動し、upload→worker ingest→検索→回答まで確認します。

## 11. Scale-to-zero / teardown

demo 停止時は task 課金を止めます。

```bash
terraform apply \
  -var='api_desired_count=0' \
  -var='worker_desired_count=0' \
  -var='qdrant_desired_count=0'
```

注意:

- desired count を 0 にしても RDS、ALB、NAT なし public networking、CloudFront、S3、CloudWatch Logs などの料金は残ります。Qdrant の service-managed EBS volume は task 停止時に削除され、次回起動時は再index前提です。
- Budget alert は `monthly_budget_limit_usd` と `budget_alert_email` で設定します。
- 完全削除は保存済みdestroy planを使う `aws-demo.ps1 down -ConfirmDestroy -DestroyConfirmation DESTROY-RUNTIME` で実行します。RDS/S3のデータ喪失を伴うため、実行前に必ず手動確認してください。

## 12. 残る検証と制約

実装済み:

1. Bedrock Runtime Converseによる回答生成
2. Titan Text Embeddings V2（1024次元）
3. Bedrock Agent Runtime Rerank
4. S3 document storageをAPI/workerで共有
5. PostgreSQL job tableのlease/retry/status維持

このPRで未実施:

- 実AWS credentialを使う `terraform plan` / `apply`
- sandbox accountでのBedrock model accessとlive invocation
- S3 upload→worker ingest→Qdrant→回答のend-to-end smoke
- task replacement後のS3/RDS保持とQdrant再index手順のlive確認

既知の運用制約:

- CloudFront→APIはVPC Originからprivate subnetのinternal ALBへHTTPで接続します。viewer TLSはCloudFront default domainで終端し、ALB SGとsecret origin headerの両方で制限します。東京リージョンでは未対応のAZ ID `apne1-az3` を除外しています。
- Qdrant service-managed EBSはtask replacementやscale-to-zeroで削除されるため、S3からの再index前提です。
- SQS/DLQは将来のwake-up通知用にpreprovisionしていますが、現行アプリは接続せず、PostgreSQL job tableがsource of truthです。


## AWS demo lifecycle entrypoint

Runtime operations use one entrypoint from the repository root:

```powershell
./deploy/aws-ecs/scripts/aws-demo.ps1 doctor
./deploy/aws-ecs/scripts/aws-demo.ps1 plan
./deploy/aws-ecs/scripts/aws-demo.ps1 up
./deploy/aws-ecs/scripts/aws-demo.ps1 load-data
./deploy/aws-ecs/scripts/aws-demo.ps1 smoke
./deploy/aws-ecs/scripts/aws-demo.ps1 status
./deploy/aws-ecs/scripts/aws-demo.ps1 down -ConfirmDestroy -DestroyConfirmation DESTROY-RUNTIME
```

The script fails closed unless all of these conditions hold:

- the checked-out branch is exactly `deploy/AWS_ECS`;
- the worktree is clean;
- the region is exactly `ap-northeast-1`;
- the active 12-digit account is listed in comma-separated `AWS_DEMO_ALLOWED_ACCOUNT_IDS`;
- the remote-state variables `TF_STATE_BUCKET`, `TF_STATE_KEY`, and `TF_LOCK_TABLE` are present;
- Terraform apply consumes a hashed saved plan whose branch, commit, account, and region match the current context.

`up` first applies the saved zero-task infrastructure plan, refreshes the external `DATABASE_URL` secret without printing it, writes fresh Terraform deployment identifiers to the runtime Secrets Manager secret, dispatches the app/frontend workflows with only the planned commit SHA, then creates and applies an exact saved scale-up plan. `load-data` uses `RAG_DEMO_BASIC_AUTH_HEADER`, `RAG_DEMO_ADMIN_EMAIL`, and `RAG_DEMO_ADMIN_PASSWORD` from the environment; do not pass those values on the command line.

`down` is intentionally destructive and requires both confirmation switches. It empties every version and delete marker from the document/frontend buckets, applies an exact saved destroy plan, deregisters CI-created task definition revisions, clears the stale database URL, and checks Terraform state plus S3, ECR, the CloudFront distribution and VPC origin, ECS, and `Lifecycle=runtime` tags for remnants. Runtime ECR repositories use `force_delete = true`; bootstrap resources do not carry the runtime lifecycle tag. It never targets `deploy/aws-ecs/bootstrap`. The bootstrap state bucket, lock table, and separately managed lifecycle OIDC role remain so the runtime can be recreated.

The `AWS Demo Lifecycle` workflow is `workflow_dispatch` only and is hard-bound to `refs/heads/deploy/AWS_ECS`. Configure `AWS_TERRAFORM_LIFECYCLE_ROLE_ARN` as a repository secret. That role is an account/bootstrap prerequisite outside the root runtime stack and its OIDC subject must be exactly:

```text
repo:<OWNER>/<REPO>:ref:refs/heads/deploy/AWS_ECS
```

Also configure `AWS_GITHUB_OIDC_PROVIDER_ARN`, `AWS_DEMO_ACCOUNT_ID`, `AWS_DEMO_ALLOWED_ACCOUNT_IDS`, the remote-state identifiers, the existing Terraform inputs, and the runtime secrets `BASIC_AUTH_HEADER`, `RAG_DEMO_ADMIN_EMAIL`, and `RAG_DEMO_ADMIN_PASSWORD` as repository secrets. The root tfvars must set `create_github_oidc_provider = false`; the script inspects saved plan JSON and rejects any runtime plan that would create or destroy the bootstrap provider. The lifecycle role must be limited to this sandbox stack and must be able to create and destroy the root Terraform resources, update the external database URL and runtime deployment-config secrets, and read the bootstrap state/lock resources.

The credential-free test is:

```powershell
./deploy/aws-ecs/scripts/aws-demo.Tests.ps1
```

It parses the PowerShell and verifies the branch, region, account allowlist, saved-plan, VPC origin/internal ALB, deployed image tag preservation, S3/ECR/task-definition/VPC-origin cleanup, runtime-only remnant filter, explicit destroy, and empty-search failure. CI does not run `up` or `down`.
