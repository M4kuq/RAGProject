locals {
  namespace_name  = "${var.name_prefix}.local"
  qdrant_dns_name = "qdrant.${local.namespace_name}"
  qdrant_url      = "http://${local.qdrant_dns_name}:6333"
  app_environment = merge(var.common_environment, {
    QDRANT_URL = local.qdrant_url
  })
  api_worker_environment = merge(local.app_environment, {
    GRAPH_STORE_PROVIDER = var.graph_store_provider
  })
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = {
    Name = "${var.name_prefix}-cluster"
  }
}

resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = local.namespace_name
  description = "Private DNS namespace for RAGProject ECS services"
  vpc         = var.vpc_id
}

resource "aws_service_discovery_service" "qdrant" {
  name = "qdrant"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.api_cpu)
  memory                   = tostring(var.api_memory)
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.api_image
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = [
        for name, value in local.api_worker_environment : {
          name  = name
          value = value
        }
      ]
      secrets = [
        for name, value_from in var.secret_environment : {
          name      = name
          valueFrom = value_from
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.api_log_group_name
          awslogs-region        = var.region
          awslogs-stream-prefix = "api"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "python -m app.scripts.healthcheck http://127.0.0.1:8000/ready database"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name_prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.worker_cpu)
  memory                   = tostring(var.worker_memory)
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.worker_image
      essential = true
      environment = [
        for name, value in local.api_worker_environment : {
          name  = name
          value = value
        }
      ]
      secrets = [
        for name, value_from in var.secret_environment : {
          name      = name
          valueFrom = value_from
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.worker_log_group_name
          awslogs-region        = var.region
          awslogs-stream-prefix = "worker"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "qdrant" {
  family                   = "${var.name_prefix}-qdrant"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.qdrant_cpu)
  memory                   = tostring(var.qdrant_memory)
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.qdrant_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name                = "qdrant-storage"
    configure_at_launch = true
  }

  container_definitions = jsonencode([
    {
      name      = "qdrant"
      image     = var.qdrant_image
      essential = true
      portMappings = [
        {
          containerPort = 6333
          hostPort      = 6333
          protocol      = "tcp"
        }
      ]
      mountPoints = [
        {
          sourceVolume  = "qdrant-storage"
          containerPath = "/qdrant/storage"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.qdrant_log_group_name
          awslogs-region        = var.region
          awslogs-stream-prefix = "qdrant"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "bash -c '</dev/tcp/127.0.0.1/6333'"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${var.name_prefix}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.api_cpu)
  memory                   = tostring(var.api_memory)
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "migration"
      image     = var.api_image
      essential = true
      command   = ["sh", "-c", "alembic upgrade head && APP_ENV=local python -m app.scripts.seed --skip-document-indexing"]
      environment = [
        for name, value in local.app_environment : {
          name  = name
          value = value
        }
      ]
      secrets = [
        for name, value_from in var.secret_environment : {
          name      = name
          valueFrom = value_from
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.api_log_group_name
          awslogs-region        = var.region
          awslogs-stream-prefix = "migration"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${var.name_prefix}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  launch_type     = "FARGATE"
  desired_count   = var.api_desired_count

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.api_security_group_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name_prefix}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  launch_type     = "FARGATE"
  desired_count   = var.worker_desired_count

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.worker_security_group_id]
    assign_public_ip = true
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
}

resource "aws_ecs_service" "qdrant" {
  name            = "${var.name_prefix}-qdrant"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.qdrant.arn
  launch_type     = "FARGATE"
  desired_count   = var.qdrant_desired_count

  volume_configuration {
    name = "qdrant-storage"

    managed_ebs_volume {
      role_arn         = var.ecs_infrastructure_role_arn
      size_in_gb       = var.qdrant_ebs_volume_size_gib
      volume_type      = "gp3"
      encrypted        = true
      file_system_type = "xfs"

      tag_specifications {
        resource_type  = "volume"
        propagate_tags = "SERVICE"
        tags = {
          Name = "${var.name_prefix}-qdrant-ebs"
        }
      }
    }
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.qdrant_security_group_id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.qdrant.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
}
