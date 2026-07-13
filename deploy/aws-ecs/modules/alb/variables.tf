variable "name_prefix" {
  description = "Name prefix for ALB resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Public subnet IDs for the ALB."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group ID for the ALB."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate ARN for the ALB HTTPS listener."
  type        = string
}

variable "origin_verify_header_name" {
  description = "Secret custom header name required before the listener forwards to the API target group."
  type        = string
}

variable "origin_verify_header_value" {
  description = "Secret custom header value required before the listener forwards to the API target group."
  type        = string
  sensitive   = true
}
