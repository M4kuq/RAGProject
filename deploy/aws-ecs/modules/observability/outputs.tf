output "api_log_group_name" {
  description = "API CloudWatch log group name."
  value       = aws_cloudwatch_log_group.api.name
}

output "worker_log_group_name" {
  description = "Worker CloudWatch log group name."
  value       = aws_cloudwatch_log_group.worker.name
}

output "qdrant_log_group_name" {
  description = "Qdrant CloudWatch log group name."
  value       = aws_cloudwatch_log_group.qdrant.name
}
