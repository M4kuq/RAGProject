variable "name_prefix" {
  description = "Name prefix for IAM resources."
  type        = string
}

variable "region" {
  description = "AWS region used for Bedrock model ARNs."
  type        = string
}

variable "github_oidc_repo" {
  description = "GitHub repository allowed to assume the deploy role, in owner/repo format."
  type        = string
}

variable "github_deploy_branch" {
  description = "GitHub branch allowed to assume the deploy role."
  type        = string
}

variable "create_github_oidc_provider" {
  description = "Whether this module creates the GitHub Actions OIDC provider."
  type        = bool
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub Actions OIDC provider ARN, or null to create or derive the standard provider ARN."
  type        = string
  default     = null
}

variable "github_oidc_thumbprints" {
  description = "Thumbprints for token.actions.githubusercontent.com."
  type        = list(string)
}

variable "ecr_repository_arns" {
  description = "ECR repository ARNs keyed by component name."
  type        = map(string)
}

variable "documents_bucket_arn" {
  description = "Documents bucket ARN."
  type        = string
}

variable "sqs_queue_arn" {
  description = "SQS queue ARN."
  type        = string
}

variable "secret_arns" {
  description = "Secrets Manager ARNs readable by ECS tasks and task execution."
  type        = list(string)
}

variable "ssm_parameter_arns" {
  description = "SSM Parameter Store ARNs readable by ECS tasks."
  type        = list(string)
}

variable "bedrock_generation_model_id" {
  description = "Bedrock generation model ID."
  type        = string
}

variable "bedrock_embedding_model_id" {
  description = "Bedrock embedding model ID."
  type        = string
}

variable "bedrock_rerank_model_id" {
  description = "Bedrock rerank model ID."
  type        = string
}
