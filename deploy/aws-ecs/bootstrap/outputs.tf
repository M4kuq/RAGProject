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
    key            = "ragproject/aws-ecs/terraform.tfstate"
    region         = var.region
    dynamodb_table = aws_dynamodb_table.locks.name
    encrypt        = true
  }
}
