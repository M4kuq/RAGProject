locals {
  name_prefix       = "${var.project}-${var.environment}"
  secret_arns       = distinct(concat([var.database_url_secret_arn, var.session_secret_arn], var.additional_secret_arns))
  api_image         = "${module.ecr.repository_urls["api"]}:${var.api_image_tag}"
  worker_image      = "${module.ecr.repository_urls["worker"]}:${var.worker_image_tag}"
  app_public_origin = coalesce(var.app_public_origin, "https://${module.cloudfront.domain_name}")
  common_app_env = {
    APP_ENV                     = var.environment
    CORS_ALLOWED_ORIGINS        = jsonencode([local.app_public_origin])
    SESSION_COOKIE_SECURE       = "true"
    SESSION_COOKIE_SAMESITE     = "lax"
    STORAGE_ROOT                = "/tmp/ragproject/uploads"
    DOCUMENTS_BUCKET_NAME       = module.s3.documents_bucket_name
    QDRANT_COLLECTION_NAME      = "document_chunks_bedrock_titan_v2"
    QDRANT_DISTANCE             = "Cosine"
    QDRANT_CREATE_COLLECTION    = "true"
    QDRANT_REQUIRED             = "false"
    QDRANT_UPSERT_BATCH_SIZE    = "64"
    QDRANT_TIMEOUT_SECONDS      = "5"
    GENERATION_PROVIDER         = "fake"
    EMBEDDING_PROVIDER          = "fake"
    RERANK_PROVIDER             = "none"
    BEDROCK_GENERATION_MODEL_ID = var.bedrock_generation_model_id
    BEDROCK_EMBEDDING_MODEL_ID  = var.bedrock_embedding_model_id
    BEDROCK_RERANK_MODEL_ID     = var.bedrock_rerank_model_id
    EMBEDDING_VECTOR_DIMENSION  = "1024"
    EMBEDDING_FAKE_DIMENSION    = "1024"
    RETRIEVAL_CACHE_ENABLED     = "false"
    NEO4J_HEALTH_CHECK_ENABLED  = "false"
    NEO4J_PROJECTION_ENABLED    = "false"
    LOG_LEVEL                   = "INFO"
    PII_MASKING_ENABLED         = "true"
    JOB_QUEUE_URL               = module.sqs.queue_url
    AWS_REGION                  = var.region
  }
}

module "network" {
  source = "./modules/network"

  name_prefix         = local.name_prefix
  vpc_cidr            = var.vpc_cidr
  public_subnet_cidrs = var.public_subnet_cidrs
}

module "ecr" {
  source = "./modules/ecr"

  name_prefix           = local.name_prefix
  image_retention_count = var.ecr_image_retention_count
}

module "sqs" {
  source = "./modules/sqs"

  name_prefix = local.name_prefix
}

module "s3" {
  source = "./modules/s3"

  name_prefix = local.name_prefix
}

module "observability" {
  source = "./modules/observability"

  name_prefix    = local.name_prefix
  retention_days = var.cloudwatch_log_retention_days
}

module "iam" {
  source = "./modules/iam"

  name_prefix                 = local.name_prefix
  region                      = var.region
  github_oidc_repo            = var.github_oidc_repo
  github_deploy_branch        = var.github_deploy_branch
  create_github_oidc_provider = var.create_github_oidc_provider
  github_oidc_provider_arn    = var.github_oidc_provider_arn
  github_oidc_thumbprints     = var.github_oidc_thumbprints
  ecr_repository_arns         = module.ecr.repository_arns
  documents_bucket_arn        = module.s3.documents_bucket_arn
  frontend_bucket_arn         = module.s3.frontend_bucket_arn
  cloudfront_distribution_arn = module.cloudfront.distribution_arn
  sqs_queue_arn               = module.sqs.queue_arn
  secret_arns                 = local.secret_arns
  ssm_parameter_arns          = var.ssm_parameter_arns
  bedrock_generation_model_id = var.bedrock_generation_model_id
  bedrock_embedding_model_id  = var.bedrock_embedding_model_id
  bedrock_rerank_model_id     = var.bedrock_rerank_model_id
}

module "rds" {
  source = "./modules/rds"

  name_prefix        = local.name_prefix
  database_name      = var.database_name
  database_username  = var.database_username
  instance_class     = var.database_instance_class
  allocated_storage  = var.database_allocated_storage
  subnet_ids         = module.network.public_subnet_ids
  security_group_ids = [module.network.rds_security_group_id]
}

module "alb" {
  source = "./modules/alb"

  name_prefix                = local.name_prefix
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.public_subnet_ids
  security_group_id          = module.network.alb_security_group_id
  origin_verify_header_name  = var.origin_verify_header_name
  origin_verify_header_value = var.origin_verify_header_value
}

module "ecs" {
  source = "./modules/ecs"

  name_prefix                 = local.name_prefix
  region                      = var.region
  vpc_id                      = module.network.vpc_id
  subnet_ids                  = module.network.public_subnet_ids
  api_security_group_id       = module.network.app_security_group_id
  worker_security_group_id    = module.network.app_security_group_id
  qdrant_security_group_id    = module.network.qdrant_security_group_id
  target_group_arn            = module.alb.target_group_arn
  execution_role_arn          = module.iam.ecs_task_execution_role_arn
  task_role_arn               = module.iam.ecs_task_role_arn
  qdrant_task_role_arn        = module.iam.qdrant_task_role_arn
  ecs_infrastructure_role_arn = module.iam.ecs_infrastructure_role_arn
  api_image                   = local.api_image
  worker_image                = local.worker_image
  qdrant_image                = var.qdrant_image
  graph_store_provider        = var.graph_store_provider
  api_cpu                     = var.api_cpu
  api_memory                  = var.api_memory
  worker_cpu                  = var.worker_cpu
  worker_memory               = var.worker_memory
  qdrant_cpu                  = var.qdrant_cpu
  qdrant_memory               = var.qdrant_memory
  qdrant_ebs_volume_size_gib  = var.qdrant_ebs_volume_size_gib
  api_desired_count           = var.api_desired_count
  worker_desired_count        = var.worker_desired_count
  qdrant_desired_count        = var.qdrant_desired_count
  common_environment          = local.common_app_env
  secret_environment = {
    DATABASE_URL   = var.database_url_secret_arn
    SESSION_SECRET = var.session_secret_arn
  }
  api_log_group_name    = module.observability.api_log_group_name
  worker_log_group_name = module.observability.worker_log_group_name
  qdrant_log_group_name = module.observability.qdrant_log_group_name

  depends_on = [module.alb]
}

module "cloudfront" {
  source = "./modules/cloudfront"

  name_prefix                          = local.name_prefix
  frontend_bucket_regional_domain_name = module.s3.frontend_bucket_regional_domain_name
  alb_dns_name                         = module.alb.dns_name
  api_path_patterns                    = var.api_path_patterns
  price_class                          = var.frontend_price_class
  basic_auth_username                  = var.basic_auth_username
  basic_auth_header_sha256             = var.basic_auth_header_sha256
  basic_auth_realm                     = var.basic_auth_realm
  origin_verify_header_name            = var.origin_verify_header_name
  origin_verify_header_value           = var.origin_verify_header_value
}

data "aws_iam_policy_document" "frontend_oac" {
  statement {
    sid     = "AllowCloudFrontReadViaOAC"
    effect  = "Allow"
    actions = ["s3:GetObject"]

    resources = [
      "${module.s3.frontend_bucket_arn}/*",
    ]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [module.cloudfront.distribution_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend_oac" {
  bucket = module.s3.frontend_bucket_id
  policy = data.aws_iam_policy_document.frontend_oac.json
}

module "budget" {
  source = "./modules/budget"

  name_prefix  = local.name_prefix
  limit_amount = var.monthly_budget_limit_usd
  alert_email  = var.budget_alert_email
}
