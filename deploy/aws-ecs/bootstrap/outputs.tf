output "state_bucket" {
  description = "S3 bucket name for Terraform remote state."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table" {
  description = "DynamoDB table name for Terraform state locking."
  value       = aws_dynamodb_table.locks.name
}

output "backend_config" {
  description = "Backend values to copy into deploy/aws-ecs/backend.tf or pass through backend-config."
  value = {
    bucket         = aws_s3_bucket.state.bucket
    key            = var.state_key
    region         = var.region
    dynamodb_table = aws_dynamodb_table.locks.name
    encrypt        = true
  }
}

output "terraform_plan_role_arn" {
  description = "GitHub OIDC role ARN for read-only runtime Terraform plans."
  value       = aws_iam_role.terraform_plan.arn
  sensitive   = true
}

output "github_oidc_provider_arn" {
  description = "Existing GitHub Actions OIDC provider ARN used by the plan role."
  value       = data.aws_iam_openid_connect_provider.github.arn
  sensitive   = true
}

output "database_url_secret_arn" {
  description = "Persistent DATABASE_URL input secret container ARN."
  value       = aws_secretsmanager_secret.input["database_url"].arn
  sensitive   = true
}

output "session_secret_arn" {
  description = "Persistent SESSION_SECRET input secret container ARN."
  value       = aws_secretsmanager_secret.input["session_secret"].arn
  sensitive   = true
}

output "demo_admin_password_secret_arn" {
  description = "Persistent demo administrator password secret container ARN."
  value       = aws_secretsmanager_secret.input["admin_password"].arn
  sensitive   = true
}
