output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID."
  value       = module.cloudfront.distribution_id
}

output "cloudfront_domain_name" {
  description = "Default CloudFront domain name for the demo."
  value       = module.cloudfront.domain_name
}

output "alb_dns_name" {
  description = "AWS-generated ALB DNS name."
  value       = module.alb.dns_name
}

output "alb_origin_domain_name" {
  description = "Route 53 domain name used by CloudFront for the HTTPS ALB origin."
  value       = var.alb_origin_domain_name
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

output "api_service_name" {
  description = "API ECS service name."
  value       = module.ecs.api_service_name
}

output "worker_service_name" {
  description = "Worker ECS service name."
  value       = module.ecs.worker_service_name
}

output "qdrant_service_name" {
  description = "Qdrant ECS service name."
  value       = module.ecs.qdrant_service_name
}

output "api_task_definition_family" {
  description = "API ECS task definition family."
  value       = module.ecs.api_task_definition_family
}

output "worker_task_definition_family" {
  description = "Worker ECS task definition family."
  value       = module.ecs.worker_task_definition_family
}

output "public_subnet_ids" {
  description = "Public subnet IDs used by Fargate tasks."
  value       = module.network.public_subnet_ids
}

output "app_security_group_id" {
  description = "Security group ID used by API and worker Fargate tasks."
  value       = module.network.app_security_group_id
}

output "migration_task_definition_arn" {
  description = "One-off schema migration ECS task definition ARN."
  value       = module.ecs.migration_task_definition_arn
}

output "migration_task_definition_family" {
  description = "One-off schema migration ECS task definition family."
  value       = module.ecs.migration_task_definition_family
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

output "alb_arn" {
  description = "ALB ARN used for post-destroy verification."
  value       = module.alb.arn
}

output "rds_identifier" {
  description = "RDS instance identifier used for post-destroy verification."
  value       = module.rds.identifier
}

output "runtime_log_group_names" {
  description = "CloudWatch log groups owned by this runtime stack."
  value = [
    module.observability.api_log_group_name,
    module.observability.worker_log_group_name,
    module.observability.qdrant_log_group_name,
  ]
}

output "runtime_iam_role_arns" {
  description = "IAM roles owned by this runtime stack."
  value = [
    module.iam.github_deploy_role_arn,
    module.iam.ecs_task_execution_role_arn,
    module.iam.ecs_task_role_arn,
    module.iam.qdrant_task_role_arn,
    module.iam.ecs_infrastructure_role_arn,
  ]
}
