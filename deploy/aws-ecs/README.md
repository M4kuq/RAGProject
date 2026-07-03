# RAGProject AWS ECS Fargate Terraform Skeleton

このディレクトリは、RAGProject を AWS ECS Fargate 上で動かすための先行 Terraform 雛形です。アプリ本体の Bedrock adapter や本番向け deploy pipeline が完成する前のレビュー用 IaC なので、この PR 時点では `terraform apply` も `terraform plan` も実行しません。

## 1. 全体アーキテクチャ

```mermaid
flowchart TD
  User[User] --> CF[CloudFront default domain<br/>CloudFront Function Basic Auth]
  CF --> S3Front[S3 frontend bucket<br/>private + OAC]
  CF --> ALB[Public ALB<br/>HTTP origin]
  ALB --> API[ECS Fargate API<br/>desired_count = 0]
  API --> RDS[(RDS PostgreSQL<br/>single-AZ)]
  API --> Qdrant[ECS Fargate Qdrant<br/>desired_count = 0 or 1]
  Worker[ECS Fargate Worker<br/>desired_count = 0] --> RDS
  Worker --> Qdrant
  API --> SQS[SQS Standard Queue]
  Worker --> SQS
  API --> Docs[S3 documents bucket]
  Worker --> Docs
  Qdrant --> EFS[(EFS access point)]
  API --> Bedrock[Amazon Bedrock<br/>Claude / Titan Embeddings V2 / Rerank]
  Worker --> Bedrock
```

Frontend は Vite/React の build artifact を S3 に置き、CloudFront OAC だけで読める private bucket とします。API は CloudFront の `/api/*`、`/health`、`/ready` から ALB HTTP origin に流します。ALB 自体は public ですが、security group は CloudFront origin-facing managed prefix list からの HTTP のみに絞ります。

## 2. モジュール構成の理由

| module | 責務 | 理由 |
|---|---|---|
| `network` | VPC、public subnet、IGW、route table、SG | NAT なし構成の境界と通信制御を一箇所で確認できる |
| `alb` | ALB、target group、listener | ECS service と CloudFront origin から独立してレビューできる |
| `ecr` | API/worker ECR repository、lifecycle | image retention と push 先を明確化する |
| `ecs` | cluster、task definition、service、Cloud Map | compute と service discovery をまとめ、desired count を一元管理する |
| `rds` | PostgreSQL、subnet group、SG attachment | DB の single-AZ/小構成を明示する |
| `sqs` | Standard queue、DLQ、redrive | async job の retry/dead-letter 設計を小さく保つ |
| `s3` | documents/frontend buckets、暗号化、versioning、public block | bucket security baseline を再利用しやすくする |
| `cloudfront` | distribution、OAC、Basic Auth Function | edge 配信と認証 gate を分離する |
| `iam` | GitHub OIDC、ECS roles、Bedrock/S3/SQS/Secrets 権限 | trust boundary と least privilege をレビューしやすくする |
| `efs` | Qdrant 永続化用 EFS/access point | Qdrant だけに永続化責務を閉じる |
| `observability` | CloudWatch log groups | retention を短くし、ログコストを見える化する |
| `budget` | AWS Budgets、SNS topic、email subscription | コスト guardrail を infrastructure と同時に用意する |

root module は各 module をつなぐ orchestration だけを持ちます。例外として、frontend bucket policy は S3 bucket と CloudFront distribution ARN の両方に依存するため、cycle を避ける目的で root に置いています。

## 3. ECS Fargate + scale-to-zero にした理由

Fargate は EC2 worker node の OS patch、capacity 管理、AMI 更新を持たないため、デモ環境の運用負担を下げられます。API、worker、Qdrant は `desired_count = 0` を default にしており、普段は task 課金を止めます。

必要なときだけ `api_desired_count = 1`、`qdrant_desired_count = 1`、ジョブ実行時に `worker_desired_count = 1` のように増やす想定です。ECS service には `ignore_changes = [desired_count]` を入れているため、手動や pipeline で一時的に desired count を変えても Terraform が即座に 0 に戻す挙動を避けます。

## 4. NAT Gateway を使わない理由と tradeoff

この雛形では NAT Gateway を作りません。Fargate task は public subnet に置き、`assign_public_ip = true` にして ECR、CloudWatch Logs、Secrets Manager、Bedrock へ直接 egress します。NAT Gateway は idle な demo 環境でも月額固定費が目立つため、先行雛形では外しています。

tradeoff は次のとおりです。

- task に public IP が付きます。ただし inbound は security group で閉じ、API は ALB 経由、Qdrant/RDS/EFS は内部 SG 参照だけに制限します。
- private subnet + NAT/VPC endpoints よりも network isolation は弱いです。本番化フェーズでは private subnet、VPC endpoints、WAF、Route53/ACM を追加する余地があります。
- RDS は `publicly_accessible = false` で public IP を持たせず、SG は ECS app SG からの 5432 のみ許可します。DB subnet group は小構成のため public subnets を使います。

## 5. Terraform state を S3 + DynamoDB にする理由

remote state は複数人・CI での差分確認や将来の apply に必要です。S3 は versioning と暗号化を有効化し、DynamoDB は state lock 用に `LockID` hash key を持ちます。

state backend 自体は Terraform state の保存先なので、root module から同時に作れません。そのため `bootstrap/` を local backend の最小構成として分離しています。

手順:

```bash
cd deploy/aws-ecs/bootstrap
terraform init
terraform fmt -check -recursive
terraform validate
# 初回構築時だけ、人間が内容を確認してから:
# terraform apply
```

bootstrap の outputs を root `backend.tf` に反映、または `-backend-config` で渡してから root を初期化します。

```bash
cd deploy/aws-ecs
terraform init -reconfigure
terraform fmt -check -recursive
terraform validate
# アプリ完成後、認証情報とコスト承認がある場合だけ:
# terraform plan
# terraform apply
```

この PR では認証情報もコスト承認もないため、`terraform init -backend=false` と `terraform validate` までに止めます。

## 6. GitHub OIDC を使う理由

GitHub Actions 用の deploy role は OIDC trust で `repo:owner/repo:ref:refs/heads/<branch>` に限定します。長期の AWS access key を GitHub Secrets に置かず、短期 STS credential だけで ECR push / ECS deploy を行う設計です。

この role は Terraform 全権限 role ではなく、雛形では image push と ECS service 更新に絞っています。将来 Terraform apply を CI から行う場合は、別 role と承認 gate を設計してください。

## 7. Bedrock keyless 設計

Claude、Titan Text Embeddings V2、Rerank は API key を持たず、ECS task role の IAM 権限で呼び出す前提です。`iam` module は次の action/resource に限定します。

- `bedrock:InvokeModel`: `bedrock_generation_model_id` と `bedrock_embedding_model_id`
- `bedrock:Rerank`: `bedrock_rerank_model_id`
- `secretsmanager:GetSecretValue`: 指定された Secret ARN のみ
- `ssm:GetParameter(s)`: 指定された Parameter ARN のみ
- S3 documents bucket と SQS job queue の必要操作のみ

現在のアプリ側 provider enum には `bedrock` がまだ無いため、ECS env の default は `GENERATION_PROVIDER=fake`、`EMBEDDING_PROVIDER=fake`、`RERANK_PROVIDER=none` にしています。Bedrock adapter 実装後に provider env を切り替える想定です。

## 8. コンポーネント別の利点・コスト・retention

| component | 利点 | demo cost 方針 |
|---|---|---|
| CloudFront | default domain で ACM/Route53 なしに HTTPS 配信できる | traffic 少量なら低額。Basic Auth Function は軽量 |
| S3 frontend | 静的 asset を private bucket + OAC で配信 | storage/requests 分のみ。versioning は rollback 用 |
| ALB | ECS API の health check と target group を提供 | desired_count 0 でも ALB 固定費は残る |
| ECS API/worker/Qdrant | server 管理なし、desired count で起動停止しやすい | default 0 で task 課金を止める |
| ECR | image scan と lifecycle で最小限保持 | default 最新 10 images のみ保持 |
| RDS PostgreSQL | source of truth。managed password で平文 secret 不要 | single-AZ、`db.t4g.micro`、backup 1 day |
| Qdrant + EFS | demo 用 vector store を task 停止後も保持 | Qdrant task は 0/1、EFS は保存量分 |
| SQS + DLQ | worker retry と失敗隔離 | request 数に応じた従量課金 |
| S3 documents | source documents の private storage | AES256、versioning enabled |
| CloudWatch Logs | task log の集約 | retention default 7 days |
| AWS Budgets + SNS | 月次 guardrail | Budget 自体は低コスト。email subscription は要承認 |
| Bedrock | keyless IAM 呼び出し | model invocation 分のみ。adapter 実装後に有効化 |

## 9. パラメータ化した箇所

- `api_image_tag` / `worker_image_tag`: image build/push 後に差し替えます。
- `database_url_secret_arn` / `session_secret_arn`: secret 値は Terraform に入れず、Secrets Manager ARN だけ渡します。
- `additional_secret_arns` / `ssm_parameter_arns`: 将来の provider token や設定値を ARN で追加できます。
- `bedrock_generation_model_id` / `bedrock_embedding_model_id` / `bedrock_rerank_model_id`: Bedrock model 切替に対応します。
- `basic_auth_header_sha256`: plaintext password ではなく、期待する `Authorization` header 全体の SHA-256 hex を渡します。
- desired count: API/worker/Qdrant を demo 時だけ起動するために変数化しています。
- log retention / ECR retention / budget amount: demo cost に合わせて調整します。

Titan Text Embeddings V2 は既存 local embedding と vector dimension が異なる可能性があります。`QDRANT_COLLECTION_NAME` は `document_chunks_bedrock_titan_v2` を default にして混在を避けていますが、adapter 実装後に実 dimension を確認し、既存 Qdrant collection は re-index してください。

## 10. 実行手順と merge 戦略

この PR で行うこと:

```bash
cd deploy/aws-ecs
terraform init -backend=false
terraform fmt -check -recursive
terraform validate

cd bootstrap
terraform init -backend=false
terraform validate
```

この PR で行わないこと:

- `terraform plan`
- `terraform apply`
- 実 AWS resource 作成
- secret 値の投入
- app code の Bedrock adapter 実装

初回 apply 前の注意:

- `database_url_secret_arn` と `session_secret_arn` は、Secret 自体を事前に作成して ARN を渡します。
- `DATABASE_URL` の secret value は RDS endpoint、DB name、user、password が確定してから人間が投入します。RDS master password は `manage_master_user_password = true` で RDS-managed secret に置かれ、Terraform files には入りません。
- `terraform.tfvars.example` の ARN と hash は placeholder です。実 secret 値や実 account id は commit しません。

merge 戦略:

1. この PR は Draft として Terraform 雛形をレビューします。
2. アプリ側で Bedrock adapter、S3 document storage、SQS worker integration の AWS 実装が揃った後に最終確認します。
3. bootstrap を人間が承認して初回 apply し、remote state backend を確定します。
4. root stack の plan をレビューし、コスト・IAM・network を再確認してから初回 apply します。
