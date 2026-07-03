output "documents_bucket_name" {
  description = "Documents bucket name."
  value       = aws_s3_bucket.this["documents"].bucket
}

output "documents_bucket_arn" {
  description = "Documents bucket ARN."
  value       = aws_s3_bucket.this["documents"].arn
}

output "frontend_bucket_id" {
  description = "Frontend bucket ID."
  value       = aws_s3_bucket.this["frontend"].id
}

output "frontend_bucket_name" {
  description = "Frontend bucket name."
  value       = aws_s3_bucket.this["frontend"].bucket
}

output "frontend_bucket_arn" {
  description = "Frontend bucket ARN."
  value       = aws_s3_bucket.this["frontend"].arn
}

output "frontend_bucket_regional_domain_name" {
  description = "Frontend bucket regional domain name for CloudFront OAC."
  value       = aws_s3_bucket.this["frontend"].bucket_regional_domain_name
}
