output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs for the internal ALB and CloudFront VPC origin."
  value       = aws_subnet.private[*].id
}

output "alb_security_group_id" {
  description = "ALB security group ID."
  value       = aws_security_group.alb.id
}

output "app_security_group_id" {
  description = "API and worker security group ID."
  value       = aws_security_group.app.id
}

output "qdrant_security_group_id" {
  description = "Qdrant security group ID."
  value       = aws_security_group.qdrant.id
}

output "rds_security_group_id" {
  description = "RDS security group ID."
  value       = aws_security_group.rds.id
}
