resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name_prefix}/api"
  retention_in_days = var.retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name_prefix}/worker"
  retention_in_days = var.retention_days
}

resource "aws_cloudwatch_log_group" "qdrant" {
  name              = "/ecs/${var.name_prefix}/qdrant"
  retention_in_days = var.retention_days
}
