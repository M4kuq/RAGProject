output "endpoint" {
  description = "RDS endpoint."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "RDS address."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "RDS port."
  value       = aws_db_instance.this.port
}

output "master_user_secret_arn" {
  description = "RDS-managed master user secret ARN."
  value       = one(aws_db_instance.this.master_user_secret[*].secret_arn)
  sensitive   = true
}

output "identifier" {
  description = "RDS instance identifier."
  value       = aws_db_instance.this.identifier
}
