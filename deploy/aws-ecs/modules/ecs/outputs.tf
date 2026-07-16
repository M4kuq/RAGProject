output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "api_service_name" {
  description = "API ECS service name."
  value       = aws_ecs_service.api.name
}

output "api_task_definition_family" {
  description = "API ECS task definition family."
  value       = aws_ecs_task_definition.api.family
}

output "worker_service_name" {
  description = "Worker ECS service name."
  value       = aws_ecs_service.worker.name
}

output "worker_task_definition_family" {
  description = "Worker ECS task definition family."
  value       = aws_ecs_task_definition.worker.family
}

output "qdrant_service_name" {
  description = "Qdrant ECS service name."
  value       = aws_ecs_service.qdrant.name
}

output "migration_task_definition_arn" {
  description = "One-off schema migration ECS task definition ARN."
  value       = aws_ecs_task_definition.migration.arn
}

output "migration_task_definition_family" {
  description = "One-off schema migration ECS task definition family."
  value       = aws_ecs_task_definition.migration.family
}

output "qdrant_url" {
  description = "Private Qdrant URL for API and worker tasks."
  value       = local.qdrant_url
}
