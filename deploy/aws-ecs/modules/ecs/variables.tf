variable "name_prefix" {
  description = "Name prefix for ECS resources."
  type        = string
}

variable "region" {
  description = "AWS region for log configuration."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for service discovery."
  type        = string
}

variable "subnet_ids" {
  description = "Public subnet IDs for Fargate tasks."
  type        = list(string)
}

variable "api_security_group_id" {
  description = "Security group ID for API tasks."
  type        = string
}

variable "worker_security_group_id" {
  description = "Security group ID for worker tasks."
  type        = string
}

variable "qdrant_security_group_id" {
  description = "Security group ID for Qdrant tasks."
  type        = string
}

variable "target_group_arn" {
  description = "ALB target group ARN for API service."
  type        = string
}

variable "execution_role_arn" {
  description = "ECS task execution role ARN."
  type        = string
}

variable "task_role_arn" {
  description = "ECS task role ARN."
  type        = string
}

variable "api_image" {
  description = "API container image."
  type        = string
}

variable "worker_image" {
  description = "Worker container image."
  type        = string
}

variable "qdrant_image" {
  description = "Qdrant container image."
  type        = string
}

variable "api_cpu" {
  description = "API task CPU units."
  type        = number
}

variable "api_memory" {
  description = "API task memory MiB."
  type        = number
}

variable "worker_cpu" {
  description = "Worker task CPU units."
  type        = number
}

variable "worker_memory" {
  description = "Worker task memory MiB."
  type        = number
}

variable "qdrant_cpu" {
  description = "Qdrant task CPU units."
  type        = number
}

variable "qdrant_memory" {
  description = "Qdrant task memory MiB."
  type        = number
}

variable "api_desired_count" {
  description = "Desired API task count."
  type        = number
}

variable "worker_desired_count" {
  description = "Desired worker task count."
  type        = number
}

variable "qdrant_desired_count" {
  description = "Desired Qdrant task count."
  type        = number
}

variable "common_environment" {
  description = "Non-secret environment variables shared by API and worker."
  type        = map(string)
}

variable "secret_environment" {
  description = "Secret environment variables for API and worker, mapping env name to Secrets Manager ARN."
  type        = map(string)
}

variable "api_log_group_name" {
  description = "API log group name."
  type        = string
}

variable "worker_log_group_name" {
  description = "Worker log group name."
  type        = string
}

variable "qdrant_log_group_name" {
  description = "Qdrant log group name."
  type        = string
}

variable "efs_file_system_id" {
  description = "EFS file system ID for Qdrant persistence."
  type        = string
}

variable "efs_access_point_id" {
  description = "EFS access point ID for Qdrant persistence."
  type        = string
}
