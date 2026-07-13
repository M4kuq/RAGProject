# RAGProject AWS ECS Fargate demo

このディレクトリは、RAGProjectをAWS ECS Fargate上で動かすdemo stackです。アカウント作成とクレジット運用は [AWS_PAID_PLAN_CREDIT_SETUP.md](./AWS_PAID_PLAN_CREDIT_SETUP.md) を参照してください。アプリはBedrock generation、Titan Embeddings V2、Bedrock Rerank、S3 document storageに対応済みです。認証不要CIでは `terraform fmt`、`init -backend=false`、`validate` だけを実行し、このPRでは実AWSの `terraform plan` / `apply` は実行しません。

## 1. 全体アーキテクチャ

```mermaid
flowchart TD
  User[User] --> CF[CloudFront default domain<br/>CloudFront Function Basic Auth]
  CF --> S3Front[S3 frontend bucket<br/>private + OAC]
  CF --> ALB[Public ALB<br/>HTTPS 443 origin + ACM]
  ALB --> API[ECS Fargate API<br/>desired_count = 0]
  API --> RDS[(RDS PostgreSQL<br/>single-AZ)]
  API --> Qdrant[ECS Fargate Qdrant<br/>desired_count = 0 or 1]
  Worker[ECS Fargate Worker<br/>desired_count = 0] --> RDS
  Worker --> Qdrant
  API -. future wake-up notification .-> SQS[SQS Standard Queue<br/>preprovisioned, not used]
  Worker -. future wake-up notification .-> SQS
  API --> Docs[S3 documents bucket<br/>shared durable source]
  Worker --> Docs
  Qdrant --> EBS[(service-managed EBS volume<br/>block storage)]
  API --> Bedrock[Amazon Bedrock<br/>Nova Lite / Titan Embeddings V2 / Rerank]
  Worker --> Bedrock
```

Frontend は Vite/React の build artifact を S3 に置き、CloudFront OAC だけで読める private bucket とします。API は CloudFront の `/api/*`、`/health`、`/ready` から証明書名と一致する Route 53 origin domain を経由して ALB HTTPS 443へ流します。ALB 自体は public ですが、security group は CloudFront origin-facing managed prefix listからのHTTPSのみに絞り、secret origin headerが一致した場合だけAPIへforwardします。

### Graph backend policy

ECS 版のグラフバックエンドは `GRAPH_STORE_PROVIDER=postgres` とし、RDS PostgreSQL 上のグラフテーブルを使います。この ECS stack には Neo4j コンテナや Neo4j 用インフラは含めません。Neo4j は EKS 本番 HA 版で StatefulSet と永続ボリュームを使う read model / projection として扱う予定で、source of truth は引き続き Postgres です。

Amazon Neptune はこの demo stack では採用しません。Neptune openCypher は Neo4j Cypher と完全互換ではなく、APOC や CONSTRAINT など Neo4j 前提のクエリ・運用を使うにはアプリ側の移植が必要です。また完全な scale-to-zero ができず、短時間 demo の固定費に合いません。将来 managed graph DB を見せる場合は、既存 Neo4j provider の置き換えではなく `NeptuneGraphStore` のような別 provider として検討します。

## 2. モジュール構成の理由

| module | 責務 | 理由 |
|---|---|---|
| `network` | VPC、public subnet、IGW、route table、SG | NAT なし構成の境界と通信制御を一箇所で確認できる |
| `alb` | ALB、target group、listener | ECS service と CloudFront origin から独立してレビューできる |
| `ecr` | API/worker ECR repository、lifecycle | image retention と push 先を明確化する |
| `ecs` | cluster、task definition、service、Cloud Map | compute と service discovery をまとめ、desired count を一元管理する |
| `rds` | PostgreSQL、subnet group、SG attachment | DB の single-AZ/小構成を明示する |
| `sqs` | Standard queue、DLQ、redrive | 将来のwake-up通知用。現行job state/leaseはPostgreSQLがsource of truth |
| `s3` | documents/frontend buckets、暗号化、versioning、public block | bucket security baseline を再利用しやすくする |
| `cloudfront` | distribution、OAC、Basic Auth Function | edge 配信と認証 gate を分離する |
| `iam` | GitHub OIDC、ECS roles、Bedrock/S3/Secrets 権限 | trust boundary と least privilege をレビューしやすくする |
| `observability` | CloudWatch log groups | retention を短くし、ログコストを見える化する |
| `budget` | AWS Budgets、SNS topic、email subscription | コスト guardrail を infrastructure と同時に用意する |

root module は各 module をつなぐ orchestration だけを持ちます。例外として、frontend bucket policy は S3 bucket と CloudFront distribution ARN の両方に依存するため、cycle を避ける目的で root に置いています。

## 3. ECS Fargate + scale-to-zero にした理由

Fargate は EC2 worker node の OS patch、capacity 管理、AMI 更新を持たないため、デモ環境の運用負担を下げられます。API、worker、Qdrant は `desired_count = 0` を default にしており、普段は task 課金を止めます。

必要なときだけ `api_desired_count = 1`、`qdrant_desired_count = 1` のように増やす想定です。Qdrant の `/qdrant/storage` は NFS/EFS ではなく、ECS service-managed EBS の `gp3` block storage を mount します。これは Qdrant の storage 要件に合わせるためです。ただし ECS service が管理する EBS volume は task replacement や scale-to-zero で削除されるため、Qdrant collection は永続的な source of truth ではありません。task 置換またはscale-to-zero後の再起動では、S3のsource documentsからBedrock Titan V2で再indexしてください。

`worker_desired_count` の既定値はscale-to-zeroのため `0` です。document upload/indexingを行う時間だけ `1` に上げられます。APIとworkerは同じS3 objectを参照し、job state/lease/retryは既存PostgreSQL job tableを使います。

## 4. NAT Gateway を使わない理由と tradeoff

この雛形では NAT Gateway を作りません。Fargate task は public subnet に置き、`assign_public_ip = true` にして ECR、CloudWatch Logs、Secrets Manager、Bedrock へ直接 egress します。NAT Gateway は idle な demo 環境でも月額固定費が目立つため、先行雛形では外しています。

tradeoff は次のとおりです。

- task に public IP が付きます。ただし inbound は security group で閉じ、API は ALB 経由、Qdrant/RDS は内部 SG 参照だけに制限します。
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

Nova Lite、Titan Text Embeddings V2、Rerank はAPI keyを持たず、ECS task roleのIAM権限で呼び出します。アプリtask roleは次のaction/resourceに限定し、Secrets Managerの読取権限は持ちません。task definitionのsecret注入だけは、containerへ公開されないECS task execution roleが行います。

- `bedrock:InvokeModel`: generation と embedding の選択済み foundation model ARN のみ
- `bedrock:Rerank`: Amazon Bedrock Rerank API は resource-level permission を提供しないため `Resource = "*"`
- `secretsmanager:GetSecretValue`: ECS task execution roleだけに許可し、task definitionが参照する指定Secret ARNのみに限定
- `ssm:GetParameter(s)`: 指定された Parameter ARN のみ
- S3 documents bucket の `source/*` object CRUD のみ

ECS env は `GENERATION_PROVIDER=bedrock`、`EMBEDDING_PROVIDER=bedrock`、`RERANK_PROVIDER=bedrock` です。generation の demo default は Tokyo で利用できる Amazon Nova Lite とし、apply 前に `GetFoundationModel` の `modelLifecycle.status` が `ACTIVE` であることを確認します。

## 8. コンポーネント別の利点・コスト・retention

| component | 利点 | demo cost 方針 |
|---|---|---|
| CloudFront | default domain で ACM/Route53 なしに HTTPS 配信できる | traffic 少量なら低額。Basic Auth Function は軽量 |
| S3 frontend | 静的 asset を private bucket + OAC で配信 | storage/requests 分のみ。versioning は rollback 用 |
| ALB | ECS API の health check と target group を提供 | desired_count 0 でも ALB 固定費は残る |
| ECS API/worker/Qdrant | server 管理なし、desired count で起動停止しやすい | default 0 で task 課金を止める |
| ECR | image scan と lifecycle で最小限保持 | default 最新 10 images のみ保持 |
| RDS PostgreSQL | source of truth。managed password で平文 secret 不要 | single-AZ、`db.t4g.micro`、backup 1 day |
| Qdrant + EBS | Qdrant 要件に合う block storage を task に attach | Qdrant task は 0/1、EBS volume は task 稼働中に service-managed で作成され、停止/置換時は再index前提 |
| SQS + DLQ | 将来のwake-up通知用。現行アプリは未接続 | 未使用時はrequest課金なし |
| S3 documents | source documents の private storage | AES256、versioning enabled |
| CloudWatch Logs | task log の集約 | retention default 7 days |
| AWS Budgets + SNS | 月次 guardrail | Budget 自体は低コスト。email subscription は要承認 |
| Bedrock | keyless IAM 呼び出し | Nova/Titan/Rerank の実行分のみ |

## 9. パラメータ化した箇所

- `api_image_tag` / `worker_image_tag`: image build/push 後に差し替えます。
- `database_url_secret_arn` / `session_secret_arn`: secret 値は Terraform に入れず、Secrets Manager ARN だけ渡します。
- `additional_secret_arns` / `ssm_parameter_arns`: 将来の provider token や設定値を ARN で追加できます。
- `bedrock_generation_model_id` / `bedrock_embedding_model_id` / `bedrock_rerank_model_id`: Bedrock model 切替に対応します。
- `graph_store_provider`: ECS 版は `postgres` を default とし、API/worker の `GRAPH_STORE_PROVIDER` に渡します。Neo4j 切替は EKS 版で扱います。
- `basic_auth_header_sha256`: plaintext password ではなく、期待する `Authorization` header 全体の SHA-256 hex を渡します。
- desired count: API/worker/Qdrant を demo 時だけ起動するために変数化しています。
- log retention / ECR retention / budget amount: demo cost に合わせて調整します。

Titan Text Embeddings V2 は1024次元で使い、`QDRANT_COLLECTION_NAME=document_chunks_bedrock_titan_v2` と一致させます。既存の異なるdimensionのcollectionは混用せず、S3 source documentsから再indexしてください。

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
- 実AWSでのBedrock model access/lifecycle確認とend-to-end smoke

初回 apply 前の注意:

- `database_url_secret_arn` と `session_secret_arn` は、Secret 自体を事前に作成して ARN を渡します。
- `DATABASE_URL` の secret value は RDS endpoint、DB name、user、password が確定してから人間が投入します。RDS master password は `manage_master_user_password = true` で RDS-managed secret に置かれ、Terraform files には入りません。
- `terraform.tfvars.example` の ARN と hash は placeholder です。実 secret 値や実 account id は commit しません。

merge 戦略:

1. この PR は Draft として Terraform 雛形をレビューします。
2. sandbox accountでS3 upload→PostgreSQL job lease→worker ingest→Bedrock/Qdrant検索のlive smokeを実施します。SQSは必要時だけwake-up通知として追加します。
3. bootstrap を人間が承認して初回 apply し、remote state backend を確定します。
4. root stack の plan をレビューし、コスト・IAM・network を再確認してから初回 apply します。

## 11. PR #88 review follow-up

この追補はアプリコードを変更せず、Terraform 側で PR #88 の review 指摘を扱うための運用前提です。

### CloudFront to ALB origin binding

CloudFront は ALB origin へ `origin_verify_header_name` / `origin_verify_header_value` を送ります。ALB listener は default `403` とし、この秘密 header が一致した場合だけ API target group へ forward します。ALB security group の CloudFront origin-facing managed prefix list はネットワーク層の絞り込みとして残しますが、この秘密 header が「この CloudFront distribution からの origin request」に束縛する制御です。

`origin_verify_header_value` は Terraform file に実値を commit せず、外部で生成したランダム値を入力してください。CloudFront と ALB listener rule の双方が apply 時に参照するため sensitive variable として扱います。

### CSRF public origin

backend の CSRF 検証は `CORS_ALLOWED_ORIGINS` を許可 origin として使います。ECS task では public HTTPS origin をこの env に設定します。default ではこの stack の CloudFront default domain を使い、custom domain や段階 rollout では `app_public_origin` を指定します。

```hcl
app_public_origin = "https://d111111abcdef8.cloudfront.net"
```

### Document storage / worker ingestion

アプリは `STORAGE_BACKEND=s3`、`DOCUMENTS_BUCKET_NAME`、`DOCUMENTS_KEY_PREFIX` を使い、APIとworkerで同じprivate S3 objectを参照します。upload時はS3 write後にPostgreSQLへjobをcommitし、DB flow失敗時はS3 objectをbest-effort削除します。workerはS3 objectを上限付き一時ファイルへmaterializeし、extract後に削除します。

非AWS環境の既定は引き続き `STORAGE_BACKEND=local` です。S3 clientは静的access keyを設定せず、AWS SDK default credential chainからECS task roleを使います。

job queueはSQSへ置き換えません。PostgreSQL job tableのlease/retry/statusがsource of truthです。SQS/DLQは将来のwake-up通知用に未接続で、task roleにも現時点ではSQS権限を付与しません。

### CloudFront to ALB TLS

CloudFrontからpublic ALB originへの通信はHTTPS onlyです。runtime stackは次を必須入力として受け取ります。

- `alb_origin_domain_name`: 例 `origin.example.com`
- `alb_certificate_arn`: `ap-northeast-1`で発行済みのACM certificate ARN
- `route53_hosted_zone_id`: origin domainを所有するpublic hosted zone

runtimeはRoute 53 aliasを現在のALBへ向け、ALBは443 listenerでACM certificateを提示します。CloudFrontは`origin_protocol_policy = "https-only"`とTLS 1.2で接続します。ALBのdefault actionは403を維持し、CloudFrontのsecret origin headerが一致した場合だけAPI target groupへforwardします。

CloudFrontのdefault domainはviewer HTTPSを既に提供します。独自viewer domain用の`us-east-1` certificateとCloudFront aliasは任意の後続対応です。ACM certificateとDNS validation recordはbootstrapとして保持し、runtime destroyへ含めません。

### Migration task

新規 RDS では API を scale out する前に one-off ECS task を実行し、schema migration 後に `--skip-document-indexing` 付き seed でログインユーザー、システム設定、seed DB rows を作成します。

```bash
terraform output -json public_subnet_ids
terraform output app_security_group_id
terraform output migration_task_definition_arn

aws ecs run-task \
  --cluster "$(terraform output -raw ecs_cluster_name)" \
  --launch-type FARGATE \
  --task-definition "$(terraform output -raw migration_task_definition_arn)" \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-REPLACE,subnet-REPLACE],securityGroups=[$(terraform output -raw app_security_group_id)],assignPublicIp=ENABLED}"
```

task command:

```bash
sh -c 'alembic upgrade head && APP_ENV=local python -m app.scripts.seed --skip-document-indexing --deployed-admin-from-env'
```

seed の `APP_ENV=local` は seed CLI の安全ガードを通すため、この command の seed 実行だけに限定します。AWS migrationでは `--deployed-admin-from-env` を併用し、Secrets Managerから注入された16文字以上の管理者passwordを使って管理者だけを作成します。既知のlocal demo passwordを持つviewerは作成せず、以前のseedで残っているlocal admin/viewerは無効化してpasswordも無作為化します。`--skip-document-indexing` によりmigration時はQdrantへ書き込まず、runtime起動後にS3のsource documentsをBedrock Titan V2でindexします。

成功後に `api_desired_count` と `qdrant_desired_count` を必要数へ変更して再 apply します。`worker_desired_count` はdocument indexingを行う間だけ増やし、処理後はscale-to-zeroへ戻してください。ECS service は `desired_count` を ignore しないため、変数変更が反映されます。

### Existing GitHub OIDC provider

AWS account に `token.actions.githubusercontent.com` provider が既にある場合は重複作成を避けます。

```hcl
create_github_oidc_provider = false
# github_oidc_provider_arn = "arn:...:oidc-provider/token.actions.githubusercontent.com"
```

`github_oidc_provider_arn` 未指定かつ作成無効の場合、IAM trust policy は現在の AWS account から導出した標準 provider ARN を使います。

### Embedding demo defaults

ECS demo は `EMBEDDING_PROVIDER=bedrock` と `BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0` を既定にし、vector dimensionは`1024`です。CI/ローカルでは引き続きfake embeddingを選択できますが、AWS runtimeのdocument indexingはBedrock Titan V2を呼び出します。初回起動前にBedrock model accessを確認し、Qdrant再作成後はS3のsource documentsを再indexしてください。


## One-command demo lifecycle

Use `scripts/aws-demo.ps1` for `doctor`, `plan`, `up`, `load-data`, `smoke`, `status`, and explicitly confirmed `down`. The scale plan preserves the deployed Git SHA, smoke rejects empty retrieval, and down removes runtime ECR images plus CI-created task definition revisions. The entrypoint is restricted to the `deploy/AWS_ECS` branch, `ap-northeast-1`, a clean worktree, and an allowlisted sandbox account. See [DEPLOY.md](./DEPLOY.md#aws-demo-lifecycle-entrypoint) for required repository variables/secrets, the persistent bootstrap lifecycle role, saved-plan checks, and verified teardown behavior.
