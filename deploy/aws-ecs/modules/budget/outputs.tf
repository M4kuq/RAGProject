output "budget_name" {
  description = "Budget name."
  value       = aws_budgets_budget.monthly.name
}

output "sns_topic_arn" {
  description = "Budget SNS topic ARN."
  value       = aws_sns_topic.budget_alerts.arn
}
