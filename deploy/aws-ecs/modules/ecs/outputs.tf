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

output "worker_service_name" {
  description = "Worker ECS service name."
  value       = aws_ecs_service.worker.name
}

output "qdrant_service_name" {
  description = "Qdrant ECS service name."
  value       = aws_ecs_service.qdrant.name
}

output "qdrant_url" {
  description = "Private Qdrant URL for API and worker tasks."
  value       = local.qdrant_url
}
