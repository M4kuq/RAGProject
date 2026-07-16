output "queue_arn" {
  description = "Main SQS queue ARN."
  value       = aws_sqs_queue.jobs.arn
}

output "queue_url" {
  description = "Main SQS queue URL."
  value       = aws_sqs_queue.jobs.url
}

output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}
