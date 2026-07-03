output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID."
  value       = module.cloudfront.distribution_id
}

output "cloudfront_domain_name" {
  description = "Default CloudFront domain name for the demo."
  value       = module.cloudfront.domain_name
}

output "alb_dns_name" {
  description = "ALB DNS name used as the CloudFront API origin."
  value       = module.alb.dns_name
}

output "api_ecr_repository_url" {
  description = "ECR repository URL for the API image."
  value       = module.ecr.repository_urls["api"]
}

output "worker_ecr_repository_url" {
  description = "ECR repository URL for the worker image."
  value       = module.ecr.repository_urls["worker"]
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = module.ecs.cluster_name
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint."
  value       = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "RDS-managed master user secret ARN."
  value       = module.rds.master_user_secret_arn
  sensitive   = true
}

output "documents_bucket_name" {
  description = "Private S3 bucket for source documents."
  value       = module.s3.documents_bucket_name
}

output "frontend_bucket_name" {
  description = "Private S3 bucket for built frontend assets."
  value       = module.s3.frontend_bucket_name
}

output "job_queue_url" {
  description = "SQS standard queue URL for async jobs."
  value       = module.sqs.queue_url
}

output "github_deploy_role_arn" {
  description = "GitHub OIDC deploy role ARN."
  value       = module.iam.github_deploy_role_arn
}
