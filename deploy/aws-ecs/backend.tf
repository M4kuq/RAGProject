# Bootstrap this backend first from deploy/aws-ecs/bootstrap.
# Then replace the bucket/table placeholders with the bootstrap outputs and run:
#   terraform init -reconfigure
# This PR intentionally validates with `terraform init -backend=false` and does not run plan/apply.
terraform {
  backend "s3" {
    bucket         = "REPLACE_WITH_BOOTSTRAP_STATE_BUCKET"
    key            = "ragproject/aws-ecs/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "REPLACE_WITH_BOOTSTRAP_LOCK_TABLE"
    encrypt        = true
  }
}
