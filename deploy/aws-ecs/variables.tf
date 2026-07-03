variable "region" {
  description = "AWS region for the demo stack."
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

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDR blocks for ALB, Fargate, RDS subnet group, and EFS mount targets."
  type        = list(string)
  default     = ["10.40.0.0/24", "10.40.1.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Exactly two public subnet CIDRs are required."
  }
}

variable "api_image_tag" {
  description = "API image tag pushed to the generated ECR repository."
  type        = string
  default     = "placeholder"
}

variable "worker_image_tag" {
  description = "Worker image tag pushed to the generated ECR repository."
  type        = string
  default     = "placeholder"
}

variable "qdrant_image" {
  description = "Qdrant container image for the demo vector store."
  type        = string
  default     = "qdrant/qdrant:v1.12.4"
}

variable "api_desired_count" {
  description = "Desired task count for the API service. Keep 0 for scale-to-zero demo baseline."
  type        = number
  default     = 0

  validation {
    condition     = var.api_desired_count >= 0
    error_message = "api_desired_count must be zero or greater."
  }
}

variable "worker_desired_count" {
  description = "Desired task count for the worker service. Keep 0 until a job run is needed."
  type        = number
  default     = 0

  validation {
    condition     = var.worker_desired_count >= 0
    error_message = "worker_desired_count must be zero or greater."
  }
}

variable "qdrant_desired_count" {
  description = "Desired task count for Qdrant. Use 0 for full scale-to-zero or 1 when indexing/searching."
  type        = number
  default     = 0

  validation {
    condition     = var.qdrant_desired_count >= 0
    error_message = "qdrant_desired_count must be zero or greater."
  }
}

variable "api_cpu" {
  description = "Fargate CPU units for the API task."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory MiB for the API task."
  type        = number
  default     = 1024
}

variable "worker_cpu" {
  description = "Fargate CPU units for the worker task."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Fargate memory MiB for the worker task."
  type        = number
  default     = 1024
}

variable "qdrant_cpu" {
  description = "Fargate CPU units for the Qdrant task."
  type        = number
  default     = 512
}

variable "qdrant_memory" {
  description = "Fargate memory MiB for the Qdrant task."
  type        = number
  default     = 1024
}

variable "database_name" {
  description = "Initial PostgreSQL database name."
  type        = string
  default     = "rag"
}

variable "database_username" {
  description = "PostgreSQL master username. Password is managed by RDS Secrets Manager, not Terraform variables."
  type        = string
  default     = "rag"
}

variable "database_instance_class" {
  description = "Small single-AZ RDS instance class for the demo environment."
  type        = string
  default     = "db.t4g.micro"
}

variable "database_allocated_storage" {
  description = "Allocated RDS storage in GiB."
  type        = number
  default     = 20
}

variable "database_url_secret_arn" {
  description = "Secrets Manager ARN containing the app DATABASE_URL value. The secret value is not stored in Terraform files."
  type        = string
}

variable "session_secret_arn" {
  description = "Secrets Manager ARN containing a strong SESSION_SECRET value. The secret value is not stored in Terraform files."
  type        = string
}

variable "app_public_origin" {
  description = "Public HTTPS origin allowed by backend CSRF checks, for example https://d111111abcdef8.cloudfront.net. Leave null to use this stack's CloudFront default domain."
  type        = string
  default     = null

  validation {
    condition     = var.app_public_origin == null || can(regex("^https://[^/]+$", var.app_public_origin))
    error_message = "app_public_origin must be an HTTPS origin without a path, for example https://d111111abcdef8.cloudfront.net."
  }
}

variable "additional_secret_arns" {
  description = "Additional Secrets Manager ARNs the ECS tasks may read."
  type        = list(string)
  default     = []
}

variable "ssm_parameter_arns" {
  description = "Optional SSM Parameter Store ARNs the ECS tasks may read."
  type        = list(string)
  default     = []
}

variable "github_oidc_repo" {
  description = "GitHub repository allowed to assume the deploy role, in owner/repo format."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_oidc_repo))
    error_message = "github_oidc_repo must be in owner/repo format."
  }
}

variable "github_deploy_branch" {
  description = "GitHub branch allowed in the OIDC trust policy for deploy operations."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "Whether to create the token.actions.githubusercontent.com OIDC provider. Set false when the AWS account already has one."
  type        = bool
  default     = true
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub Actions OIDC provider ARN. When null, the module creates one or uses the standard account-local ARN if create_github_oidc_provider=false."
  type        = string
  default     = null

  validation {
    condition     = var.github_oidc_provider_arn == null || can(regex("^arn:[^:]+:iam::[0-9]{12}:oidc-provider/.+", var.github_oidc_provider_arn))
    error_message = "github_oidc_provider_arn must be an IAM OIDC provider ARN."
  }
}

variable "github_oidc_thumbprints" {
  description = "Thumbprints for token.actions.githubusercontent.com. Verify before first apply because this can change."
  type        = list(string)
  default     = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

variable "bedrock_generation_model_id" {
  description = "Bedrock generation foundation model ID, for example an Anthropic Claude Sonnet model."
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20240620-v1:0"
}

variable "bedrock_embedding_model_id" {
  description = "Bedrock embedding foundation model ID. Titan Text Embeddings V2 changes vector dimension versus local defaults."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "bedrock_rerank_model_id" {
  description = "Bedrock rerank model ID."
  type        = string
  default     = "amazon.rerank-v1:0"
}

variable "basic_auth_username" {
  description = "Basic authentication username used only for operator documentation and CloudFront Function comments."
  type        = string
}

variable "basic_auth_header_sha256" {
  description = "SHA-256 hex digest of the full expected Authorization header, e.g. 'Basic base64(username:password)'. Do not store plaintext passwords."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.basic_auth_header_sha256))
    error_message = "basic_auth_header_sha256 must be a 64-character lowercase SHA-256 hex digest."
  }
}

variable "basic_auth_realm" {
  description = "Basic authentication realm shown by CloudFront."
  type        = string
  default     = "RAGProject Demo"
}

variable "origin_verify_header_name" {
  description = "Secret custom header name CloudFront adds to API origin requests and the ALB listener requires before forwarding."
  type        = string
  default     = "X-RAGProject-Origin-Verify"

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+$", var.origin_verify_header_name))
    error_message = "origin_verify_header_name must be a valid HTTP header token using letters, digits, and hyphens."
  }
}

variable "origin_verify_header_value" {
  description = "Secret custom header value used to bind this CloudFront distribution to the ALB origin. Generate a random value and do not commit it."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.origin_verify_header_value) >= 32 && !can(regex("[*?]", var.origin_verify_header_value))
    error_message = "origin_verify_header_value must be at least 32 characters and must not contain ALB wildcard characters * or ?."
  }
}

variable "budget_alert_email" {
  description = "Email address subscribed to the budget SNS topic. Leave null to create SNS topic and budget without email subscription."
  type        = string
  default     = null
}

variable "monthly_budget_limit_usd" {
  description = "Monthly budget limit in USD for demo guardrails."
  type        = string
  default     = "30"
}

variable "cloudwatch_log_retention_days" {
  description = "CloudWatch Logs retention in days for short-lived demo logs."
  type        = number
  default     = 7
}

variable "ecr_image_retention_count" {
  description = "Number of recent ECR images to retain per repository."
  type        = number
  default     = 10
}

variable "frontend_price_class" {
  description = "CloudFront price class for the demo distribution."
  type        = string
  default     = "PriceClass_200"
}

variable "api_path_patterns" {
  description = "CloudFront path patterns routed to the ALB/API origin."
  type        = list(string)
  default     = ["/api/*", "/health", "/ready"]
}
