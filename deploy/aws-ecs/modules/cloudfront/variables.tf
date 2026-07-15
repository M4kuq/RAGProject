variable "name_prefix" {
  description = "Name prefix for CloudFront resources."
  type        = string
}

variable "frontend_bucket_regional_domain_name" {
  description = "Regional domain name of the private frontend S3 bucket."
  type        = string
}

variable "alb_arn" {
  description = "Internal ALB ARN used by the CloudFront VPC origin."
  type        = string
}

variable "alb_dns_name" {
  description = "AWS-generated internal ALB DNS name."
  type        = string
}

variable "api_path_patterns" {
  description = "Path patterns routed to the API origin."
  type        = list(string)
}

variable "price_class" {
  description = "CloudFront price class."
  type        = string
}

variable "basic_auth_username" {
  description = "Basic auth username for documentation/commenting."
  type        = string
}

variable "basic_auth_header_sha256" {
  description = "SHA-256 hex digest of the expected Basic Authorization header."
  type        = string
  sensitive   = true
}

variable "basic_auth_realm" {
  description = "Basic auth realm."
  type        = string
}

variable "origin_verify_header_name" {
  description = "Secret custom header name CloudFront sends to the ALB origin."
  type        = string
}

variable "origin_verify_header_value" {
  description = "Secret custom header value CloudFront sends to the ALB origin."
  type        = string
  sensitive   = true
}
