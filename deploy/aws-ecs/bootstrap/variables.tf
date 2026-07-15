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

variable "state_key" {
  description = "S3 object key for the runtime Terraform state."
  type        = string
  default     = "ragproject/aws-ecs/terraform.tfstate"
}

variable "github_oidc_repo" {
  description = "GitHub repository allowed to assume the Terraform plan role."
  type        = string
  default     = "M4kuq/RAGProject"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_oidc_repo))
    error_message = "github_oidc_repo must use the owner/repository format."
  }
}

variable "github_deploy_branch" {
  description = "GitHub branch allowed to assume the Terraform plan role."
  type        = string
  default     = "deploy/AWS_ECS"

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]+$", var.github_deploy_branch))
    error_message = "github_deploy_branch contains unsupported characters."
  }
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub Actions OIDC provider ARN. The standard account-local ARN is derived when null."
  type        = string
  default     = null

  validation {
    condition     = var.github_oidc_provider_arn == null || can(regex("^arn:[^:]+:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.github_oidc_provider_arn))
    error_message = "github_oidc_provider_arn must identify the standard GitHub Actions OIDC provider."
  }
}
