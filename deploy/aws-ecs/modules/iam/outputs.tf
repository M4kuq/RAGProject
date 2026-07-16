output "github_deploy_role_arn" {
  description = "GitHub OIDC deploy role ARN."
  value       = aws_iam_role.github_deploy.arn
}

output "ecs_task_execution_role_arn" {
  description = "ECS task execution role ARN."
  value       = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_role_arn" {
  description = "ECS task role ARN."
  value       = aws_iam_role.ecs_task.arn
}

output "qdrant_task_role_arn" {
  description = "Qdrant ECS task role ARN with no application permissions attached."
  value       = aws_iam_role.qdrant_task.arn
}

output "ecs_infrastructure_role_arn" {
  description = "ECS infrastructure role ARN for service-managed EBS volumes."
  value       = aws_iam_role.ecs_infrastructure.arn
}
