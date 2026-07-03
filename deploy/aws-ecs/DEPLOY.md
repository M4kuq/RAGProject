# AWS ECS Fargate デプロイ手順

この runbook は `deploy/AWS_ECS` branch の AWS demo stack を、アプリケーションソースコードを変更せずに配備するための手順です。CI は GitHub OIDC の短期認証だけを使い、静的 AWS access key は使いません。

## 1. 前提

- AWS account は demo 用に分離することを推奨します。
- Region は `ap-northeast-1` を使います。
- GitHub Actions の deploy 対象 branch は Terraform 変数 `github_deploy_branch` で固定します。PR #88 の branch で動かす場合は `deploy/AWS_ECS` にします。
- AWS Console で Bedrock model access を有効化します。
  - Claude generation model: `bedrock_generation_model_id`
  - Titan Text Embeddings V2: `amazon.titan-embed-text-v2:0`
  - Bedrock Rerank: `bedrock_rerank_model_id`
- `terraform apply` は CI では実行しません。CI は `terraform plan` まで、初回 bootstrap と root apply は人手でレビューして実行します。

## 2. Secrets Manager と秘密値

RDS master password は `manage_master_user_password = true` により RDS managed secret になります。Terraform file や GitHub secrets に RDS master password は置きません。

Terraform が ECS task definition に ARN として渡す Secrets Manager secret:

| secret | 用途 | 作成タイミング |
|---|---|---|
| `DATABASE_URL` | API/worker の DB 接続文字列 | root apply 前に secret だけ作成し、RDS 作成後に値を更新 |
| `SESSION_SECRET` | session cookie 署名用の強いランダム値 | root apply 前 |
| `additional_secret_arns` | 将来の provider token など | 必要になった時だけ |

Terraform sensitive variable または GitHub Actions secret として渡す値:

| 値 | 用途 | GitHub Actions |
|---|---|---|
| `basic_auth_header_sha256` | CloudFront Function の Basic 認証判定。`Authorization` header 全体の SHA-256 hex | `secrets.BASIC_AUTH_HEADER_SHA256` |
| `origin_verify_header_value` | CloudFront から ALB origin への private header 値 | `secrets.ORIGIN_VERIFY_HEADER_VALUE` |

これらの plaintext は commit しません。Basic 認証 password、origin verify header value、`.env`、cookie、token は PR や workflow log に出さないでください。

## 3. GitHub Actions variables / secrets

GitHub repository の `Settings > Secrets and variables > Actions` に設定します。

### Variables

| name | 取得元 |
|---|---|
| `AWS_REGION` | `ap-northeast-1` |
| `AWS_TERRAFORM_PLAN_ROLE_ARN` | 人手で用意した read-only Terraform plan 用 OIDC role ARN |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw github_deploy_role_arn` |
| `TF_STATE_BUCKET` | `deploy/aws-ecs/bootstrap` の `terraform output -raw state_bucket` |
| `TF_LOCK_TABLE` | `deploy/aws-ecs/bootstrap` の `terraform output -raw lock_table` |
| `TF_STATE_KEY` | `terraform output -json backend_config` の `key`。既定は `ragproject/aws-ecs/terraform.tfstate` |
| `GITHUB_OIDC_REPO` | `OWNER/REPO`。Terraform 変数 `github_oidc_repo` と同じ値 |
| `GITHUB_DEPLOY_BRANCH` | `deploy/AWS_ECS`。Terraform 変数 `github_deploy_branch` と同じ値 |
| `DATABASE_URL_SECRET_ARN` | 手作成した `DATABASE_URL` secret ARN |
| `SESSION_SECRET_ARN` | 手作成した `SESSION_SECRET` secret ARN |
| `BASIC_AUTH_USERNAME` | Terraform 変数 `basic_auth_username` と同じ値 |
| `API_ECR_REPOSITORY_URL` | `terraform output -raw api_ecr_repository_url` |
| `WORKER_ECR_REPOSITORY_URL` | `terraform output -raw worker_ecr_repository_url` |
| `ECS_CLUSTER_NAME` | `terraform output -raw ecs_cluster_name` |
| `ECS_PUBLIC_SUBNET_IDS_JSON` | `terraform output -json public_subnet_ids` |
| `ECS_APP_SECURITY_GROUP_ID` | `terraform output -raw app_security_group_id` |
| `ECS_API_SERVICE_NAME` | `terraform output -raw api_service_name` |
| `ECS_WORKER_SERVICE_NAME` | `terraform output -raw worker_service_name` |
| `ECS_API_TASK_DEFINITION` | `terraform output -raw api_task_definition_family` |
| `ECS_WORKER_TASK_DEFINITION` | `terraform output -raw worker_task_definition_family` |
| `ECS_MIGRATION_TASK_DEFINITION` | `terraform output -raw migration_task_definition_family` |
| `FRONTEND_BUCKET_NAME` | `terraform output -raw frontend_bucket_name` |
| `CLOUDFRONT_DISTRIBUTION_ID` | `terraform output -raw cloudfront_distribution_id` |

### Secrets

| name | 内容 |
|---|---|
| `BASIC_AUTH_HEADER_SHA256` | `Basic base64(username:password)` header 文字列の SHA-256 hex |
| `ORIGIN_VERIFY_HEADER_VALUE` | 32 文字以上のランダム値。Terraform 変数 `origin_verify_header_value` と同じ値 |

`AWS_TERRAFORM_PLAN_ROLE_ARN` は deploy role と分けます。plan role には remote state の read/lock と、この stack の plan に必要な read-only 権限だけを付けます。deploy role は `terraform apply` 権限を持たず、ECR push、ECS deploy、migration run、frontend S3 sync、CloudFront invalidation だけに使います。

AWS 認証を伴う `AWS Infra Plan` は同一 repository の PR または `workflow_dispatch` で使います。Fork PR には remote state や GitHub secrets を渡さず、認証不要の通常 CI と人手レビューで確認します。

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

account ID や実 ARN は workflow file に書かず、GitHub variables か Terraform output から渡します。

## 5. State bootstrap

remote state backend は root stack 自身からは作れないため、先に `bootstrap/` を local backend で apply します。

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

Secrets Manager secret ARN、Basic 認証 hash、origin verify header、GitHub OIDC 設定を tfvars か安全な変数注入で渡して実行します。

```bash
cd deploy/aws-ecs
terraform init -reconfigure
terraform fmt -check -recursive
terraform validate
terraform plan
# plan とコストをレビューしてから人手または承認付き手順で実行
terraform apply
```

CI workflow `AWS Infra Plan` は `terraform fmt`、`init`、`validate`、`plan` までです。`terraform apply` は workflow に入れません。

初回 root apply 後、RDS managed master secret から password を確認し、手作成済み `DATABASE_URL` secret の値を実 DB endpoint に更新します。secret 値は log や PR に出さないでください。

## 7. 初回アプリデプロイ

1. `AWS Deploy App` workflow を `deploy/AWS_ECS` branch から手動実行します。
2. `image_tag` を空にすると workflow 実行コミット SHA が使われます。任意の tag を指定することもできます。
3. workflow は API image と worker image を `backend/Dockerfile` の `backend` / `worker` target で build し、ECR に push します。
4. workflow は Terraform が作った migration task definition family を base に、新しい API image tag の revision を登録します。
5. migration one-off task で `alembic upgrade head` を実行し、停止を待って `migration` container の exit code が `0` であることを確認します。
6. migration 成功後に API/worker task definition revision を登録し、API/worker service を新 revision に更新します。
7. この workflow は `desired_count` を変更しません。scale-to-zero demo 方針に合わせ、起動が必要な時だけ Terraform 変数または AWS CLI で明示的に増やします。

起動する場合の例:

```bash
terraform apply \
  -var='api_desired_count=1' \
  -var='qdrant_desired_count=1' \
  -var='worker_desired_count=0'
```

`worker_desired_count` は S3 document storage adapter がアプリ側に入るまで `0` を維持します。

## 8. Task definition 所有権

Terraform は ECS task definition family と baseline revision を所有します。CI はその family の最新 ACTIVE revision を `describe-task-definition` し、container image だけを差し替えた deploy revision を `register-task-definition` します。

このため、次回 `terraform apply` で `api_image_tag` / `worker_image_tag` が古いままだと、Terraform が service を Terraform 管理 revision に戻す可能性があります。運用方針は次のどちらかに統一します。

- CI deploy 後に、次回 apply 前へ `api_image_tag` / `worker_image_tag` を同じ tag に更新する。
- demo 中は Terraform を infrastructure baseline 管理に使い、アプリ image revision は CI 側の一時 revision として扱う。

どちらの場合も migration は service 更新より先に実行します。

## 9. フロント配信

`AWS Deploy Frontend` workflow を `deploy/AWS_ECS` branch から手動実行します。

フロントエンドは S3 + CloudFront の静的配信です。ECS の frontend container は使いません。

workflow の処理:

1. OIDC で `AWS_DEPLOY_ROLE_ARN` を assume します。
2. `frontend` で `npm ci`、`npm run build` を実行します。
3. `frontend/dist` を `s3://$FRONTEND_BUCKET_NAME` に `aws s3 sync --delete` します。
4. `CLOUDFRONT_DISTRIBUTION_ID` に `create-invalidation --paths "/*"` を実行します。

疎通確認:

```bash
terraform output -raw cloudfront_domain_name
```

表示された CloudFront domain に HTTPS でアクセスし、Basic 認証後に画面と `/ready` 系の API routing を確認します。

## 10. Seed / indexing

seed と document indexing は Bedrock adapter 有効化後に実行します。

現時点の ECS env は安全側の default として `GENERATION_PROVIDER=fake`、`EMBEDDING_PROVIDER=fake`、`RERANK_PROVIDER=none` です。fake embedding のまま seed/indexing すると、Titan V2 用の `document_chunks_bedrock_titan_v2` collection に fake vector が混ざります。

Bedrock 3 adapter が揃ってから、Titan Text Embeddings V2 の 1024 次元に合わせて Qdrant collection を再作成または再index してください。

## 11. Scale-to-zero / teardown

demo 停止時は task 課金を止めます。

```bash
terraform apply \
  -var='api_desired_count=0' \
  -var='worker_desired_count=0' \
  -var='qdrant_desired_count=0'
```

注意:

- desired count を 0 にしても RDS、EFS、ALB、NAT なし public networking、CloudFront、S3、CloudWatch Logs などの料金は残ります。
- Budget alert は `monthly_budget_limit_usd` と `budget_alert_email` で設定します。
- 完全削除は `terraform destroy` ですが、RDS/EFS/S3 のデータ喪失を伴うため、実行前に必ず手動確認してください。

## 12. 今回対象外のアプリコード前提

この PR は deploy 配管だけを追加します。backend/frontend/worker のアプリケーションソースコードは変更しません。

残る前提:

1. Bedrock 3 adapter
   - 生成: Bedrock Converse
   - 埋め込み: Titan Text Embeddings V2
   - rerank: Bedrock Rerank
2. S3 document storage adapter
   - 現状は `LocalFileStorage` のみです。
   - API task と worker task の local filesystem は共有されません。
   - この adapter が入るまで `worker_desired_count=0` を維持します。
3. Titan 次元での Qdrant 再index
   - Titan V2 の 1024 次元 collection を本番データで作り直します。

これらが揃うまでは、API は起動して疎通できますが、LLM 回答パスと worker ingestion は未完です。
