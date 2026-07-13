# AWS新規アカウント・Paid Planクレジット利用手順

更新日: 2026-07-13  
対象ブランチ: `deploy/AWS_ECS`

## 採用方針

- 新規AWSアカウントをstandaloneで作成する。
- サインアップ時は**Paid Plan**を選ぶ。
- 新規顧客向けの初回USD 100クレジットと、指定アクティビティによる追加最大USD 100を利用する。
- Paid Planでは全AWSサービスを利用できるが、クレジットを使い切った後やクレジット対象外料金は登録カードへ従量課金される。
- runtimeは検証時だけ作成し、同じ日のうちにdestroyする。
- Terraform state、lock、GitHub OIDC、ACM証明書、DNS validationはbootstrapとして保持する。
- クレジット利用を優先する期間はAWS Organizations / Control Towerを使わない。

公式情報:

- [Choosing an AWS Free Tier plan](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
- [AWS Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)
- [Earning additional credits](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans-activities.html)
- [AWS Free Tier Terms](https://aws.amazon.com/free/terms/)

## 1. アカウント作成

1. [AWS Free Tier](https://aws.amazon.com/free/)から新規登録する。
2. AWSで未使用のメールアドレスを指定する。
3. Account nameは`RAGProject-Sandbox`などにする。
4. 学習・個人検証ならPersonalを選ぶ。
5. Planは**Paid Plan**、SupportはBasicを選ぶ。
6. 支払方法、連絡先、本人確認を登録する。
7. Billing and Cost Managementで以下を確認し、秘密情報を含めず手元に記録する。
   - Credit balance
   - Credit expiration date
   - Account planがPaid Planであること
   - Explore AWS widgetの追加クレジット条件

Paid Planでは利用料金にクレジットが先に適用され、クレジットを使い切った後、または対象外料金が発生した場合に登録カードへ請求される。

## 2. rootとIAMの保護

1. rootへpasskey、セキュリティキー、またはMFAを設定する。
2. root Access Keyを作らない。
3. rootメールアドレスと回復用電話番号を最新状態にする。
4. rootで日常操作用IAMユーザー`ragproject-admin`を作る。
5. IAMユーザーのConsole accessとMFAを有効化する。
6. 初回bootstrapの間だけ`AdministratorAccess`と`SignInLocalDevelopmentAccess`を付与する。
7. IAM Access Keyは作成しない。
8. bootstrap完了後にローカル権限を縮小する。

公式情報: [Root user best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/root-user-best-practices.html)

## 3. クレジットとBudget

追加クレジットはAWS Consoleの表示条件と期限を正本とする。公式例にはBudgets、Bedrock playground、RDS、EC2、Lambdaが含まれる。本プロジェクトと重なるBudgets、Bedrock、RDSを優先する。

推奨Budget:

- Monthly cost budget: USD 25
- Actual 50%、80%、100%でメール通知
- Forecasted 100%でメール通知
- Credit balanceをデプロイ前、destroy直後、翌日に確認

BudgetとBilling情報には反映遅延があり、厳密なリアルタイム上限ではない。クレジット対象外サービスは残高があってもカード請求され得る。

## 4. ACMとドメイン

HTTPSにはLet's Encryptではなく、AWS Certificate Managerの非export型公開証明書を使う。

推奨DNS名:

- CloudFront viewer: `app.<your-domain>`
- ALB origin: `origin.<your-domain>`

証明書:

1. `us-east-1`で`app.<your-domain>`用CloudFront viewer証明書をDNS validationで作る。
2. `ap-northeast-1`で`origin.<your-domain>`用ALB証明書をDNS validationで作る。
3. 両方が`Issued`になるまでapplyしない。
4. DNS validation CNAMEを削除しない。
5. ACM証明書とvalidation recordはbootstrap資源として保持し、runtime destroyへ含めない。

既存ドメインのサブドメイン利用を推奨する。Route 53のドメイン登録・移管・更新料にはAWS promotional creditを使えず、登録カードへ請求される。

公式情報:

- [ACM pricing](https://aws.amazon.com/certificate-manager/pricing/)
- [ACM DNS validation](https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html)
- [AWS promotional credit exclusions](https://aws.amazon.com/awscredits/)
- [Route 53 domain registration](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-register.html)

## 5. AWS CLI短期認証

AWS CLI v2.32.0以降の`aws login`を使い、長期Access Keyを保存しない。

~~~powershell
aws login --profile ragproject-paid

$env:AWS_PROFILE = "ragproject-paid"
$env:AWS_REGION = "ap-northeast-1"
$env:AWS_DEFAULT_REGION = "ap-northeast-1"

aws sts get-caller-identity --profile ragproject-paid
$env:AWS_DEMO_ALLOWED_ACCOUNT_IDS = "<12-digit-account-id>"
~~~

公式情報: [Login for AWS local development](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)

root/IAM password、MFA code、Access Key、Secret Access Key、Session tokenはリポジトリ、GitHub、チャットへ保存しない。

## 6. 初回apply前の確認

- Account IDが`AWS_DEMO_ALLOWED_ACCOUNT_IDS`と一致する。
- Regionが`ap-northeast-1`である。
- `deploy/AWS_ECS`worktreeがcleanである。
- Credit balanceと有効期限に余裕がある。
- ACM証明書が`Issued`である。
- Nova Lite、Titan Text Embeddings V2、Bedrock rerankが東京リージョンで利用できる。
- RDS instance classが必要最小限である。
- Terraform planに想定外のNAT Gateway、高額instance、長期保持resourceがない。
- runtime planがOIDC、state、lock、ACM、DNS validationを削除しない。
- HTTPS対応後はALB HTTPS 443 listenerとCloudFront `https-only`を確認する。

## 7. 永続bootstrap

次は一度だけ作成して保持する。

- Terraform state用S3
- Terraform lock
- GitHub OIDC provider
- lifecycle/deploy role
- Secrets Managerのsecret container
- ACM certificates
- DNS validation records
- 必要なRoute 53 hosted zone

GitHubにはAccess Keyを登録せずOIDCを使う。trustは`M4kuq/RAGProject`、`refs/heads/deploy/AWS_ECS`、手動`workflow_dispatch`に限定する。

runtime側のTerraform planがGitHub OIDC providerをcreate/destroyしようとした場合、`aws-demo.ps1`は処理を拒否する。

## 8. runtime操作

~~~powershell
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 doctor
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 plan
~~~

planを人間が確認後:

~~~powershell
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 up
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 load-data
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 smoke
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 status
~~~

結果回収後、同じ日のうちにdestroyする。

~~~powershell
pwsh deploy/aws-ecs/scripts/aws-demo.ps1 down -ConfirmDestroy -DestroyConfirmation DESTROY-RUNTIME
~~~

destroy後はECS、RDS、ALB、CloudFront、ECR、runtime S3、Logsが残っていないことを確認する。state、lock、OIDC、ACM、DNS validationだけをbootstrapとして残す。Billingは反映遅延があるため翌日も確認する。

## 9. 中止条件

- Account IDやRegionが不一致。
- Credit balance不足、有効期限間近、Budget alert発生。
- ACMが`Issued`ではない。
- Bedrock modelが利用できない。
- planがbootstrapを削除する。
- 想定外のresource、instance size、NAT Gatewayが含まれる。
- worktreeがdirty。
- destroy後の残存resource検査が失敗した。

## 10. クレジット終了前

クレジット失効日の30日前までに、継続するか判断する。

継続しない場合:

- runtimeをdestroyする。
- 必要な結果だけローカルへ保存する。
- domain更新設定を確認する。
- bootstrapも不要なら別手順で明示的に削除する。
- Billingとresource inventoryを確認してからアカウントを閉じる。

継続する場合:

- Budget / Budget Actionを再設定する。
- Organizationsを採用するか再設計する。
- production前にVPC Origin/internal ALB、least privilege、監査ログ、backupを再設計する。

Paid Plan継続、Organizations参加、bootstrap削除は自動化せず、必ず人間が明示判断する。

## 11. Codexへ共有してよい情報

- 12桁のAccount ID
- ドメイン名
- AWS CLI profile名
- Credit balanceの概算と失効日
- ACM certificate ARN
- Hosted zone ID
- GitHub OIDC role ARN

共有しない情報:

- root/IAM password
- MFA code
- Access Key / Secret Access Key / Session token
- 支払情報
- Basic Authやアプリ管理者の平文password
- secret value
