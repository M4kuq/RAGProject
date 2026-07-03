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
