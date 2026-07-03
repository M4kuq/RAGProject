variable "region" {
  description = "AWS region for Terraform state bootstrap resources."
  type        = string
  default     = "ap-northeast-1"
}

variable "project" {
  description = "Project tag and naming prefix."
  type        = string
  default     = "ragproject"
}

variable "environment" {
  description = "Environment tag and naming suffix."
  type        = string
  default     = "demo"
}

variable "state_bucket_prefix" {
  description = "Globally unique S3 bucket prefix for Terraform state. AWS appends a random suffix."
  type        = string
  default     = "ragproject-demo-terraform-state-"
}

variable "lock_table_name" {
  description = "DynamoDB table name for Terraform state locking."
  type        = string
  default     = "ragproject-demo-terraform-locks"
}
