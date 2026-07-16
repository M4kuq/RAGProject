locals {
  runtime_name_prefix = "${var.project}-${var.environment}"

  runtime_role_names = toset([
    "${local.runtime_name_prefix}-github-deploy",
    "${local.runtime_name_prefix}-ecs-execution",
    "${local.runtime_name_prefix}-ecs-task",
    "${local.runtime_name_prefix}-qdrant-task",
    "${local.runtime_name_prefix}-ecs-infra",
  ])
  runtime_role_arns = [
    for name in local.runtime_role_names :
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${name}"
  ]
  runtime_pass_role_arns = [
    for name in [
      "${local.runtime_name_prefix}-ecs-execution",
      "${local.runtime_name_prefix}-ecs-task",
      "${local.runtime_name_prefix}-qdrant-task",
      "${local.runtime_name_prefix}-ecs-infra",
    ] :
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${name}"
  ]

  runtime_policy_names = toset([
    "${local.runtime_name_prefix}-github-deploy",
    "${local.runtime_name_prefix}-ecs-execution-secrets",
    "${local.runtime_name_prefix}-ecs-task",
  ])
  runtime_policy_arns = [
    for name in local.runtime_policy_names :
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${name}"
  ]
  runtime_attachable_policy_arns = concat(local.runtime_policy_arns, [
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSInfrastructureRolePolicyForVolumes",
  ])

  runtime_bucket_arns = [
    "arn:${data.aws_partition.current.partition}:s3:::${local.runtime_name_prefix}-documents-*",
    "arn:${data.aws_partition.current.partition}:s3:::${local.runtime_name_prefix}-frontend-*",
  ]
  runtime_bucket_object_arns = [for arn in local.runtime_bucket_arns : "${arn}/*"]

  runtime_deployment_config_secret_arn = "arn:${data.aws_partition.current.partition}:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${local.runtime_name_prefix}-deployment-config-*"
  rds_master_secret_arn                = "arn:${data.aws_partition.current.partition}:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:rds!db-*"
}

data "aws_iam_policy_document" "terraform_lifecycle_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_oidc_repo}:ref:refs/heads/${var.github_deploy_branch}"]
    }
  }
}

resource "aws_iam_role" "terraform_lifecycle" {
  name                 = "${local.runtime_name_prefix}-terraform-lifecycle"
  assume_role_policy   = data.aws_iam_policy_document.terraform_lifecycle_assume.json
  max_session_duration = 3600

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = "${local.runtime_name_prefix}-terraform-lifecycle"
    Component = "terraform-lifecycle"
    Lifecycle = "bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "terraform_lifecycle_read_only" {
  role       = aws_iam_role.terraform_lifecycle.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "terraform_lifecycle_state" {
  statement {
    sid    = "ReadTerraformStateBucket"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:ListBucket",
      "s3:ListBucketVersions",
    ]
    resources = [aws_s3_bucket.state.arn]

    condition {
      test     = "StringEquals"
      variable = "s3:prefix"
      values   = [var.state_key]
    }
  }

  statement {
    sid    = "WriteTerraformStateObject"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.state.arn}/${var.state_key}"]
  }

  statement {
    sid    = "LockTerraformState"
    effect = "Allow"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.locks.arn]
  }
}

resource "aws_iam_role_policy" "terraform_lifecycle_state" {
  name   = "${local.runtime_name_prefix}-terraform-lifecycle-state"
  role   = aws_iam_role.terraform_lifecycle.name
  policy = data.aws_iam_policy_document.terraform_lifecycle_state.json
}

data "aws_iam_policy_document" "terraform_lifecycle_iam" {
  statement {
    sid    = "ManageRuntimeRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = local.runtime_role_arns
  }

  statement {
    sid    = "ManageRuntimePolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = local.runtime_policy_arns
  }

  statement {
    sid    = "AttachOnlyApprovedRuntimePolicies"
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = local.runtime_role_arns

    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = local.runtime_attachable_policy_arns
    }
  }

  statement {
    sid       = "PassOnlyRuntimeEcsRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = local.runtime_pass_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "ecs-tasks.amazonaws.com",
        "ecs.amazonaws.com",
      ]
    }
  }

  statement {
    sid       = "CreateOnlyRequiredServiceLinkedRoles"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::*:role/aws-service-role/*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values = [
        "ecs.amazonaws.com",
        "elasticloadbalancing.amazonaws.com",
        "rds.amazonaws.com",
        "vpcorigin.cloudfront.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_policy" "terraform_lifecycle_iam" {
  name        = "${local.runtime_name_prefix}-terraform-lifecycle-iam"
  description = "Manage only the IAM roles and policies owned by the RAGProject demo runtime."
  policy      = data.aws_iam_policy_document.terraform_lifecycle_iam.json

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = "${local.runtime_name_prefix}-terraform-lifecycle-iam"
    Component = "terraform-lifecycle"
    Lifecycle = "bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "terraform_lifecycle_iam" {
  role       = aws_iam_role.terraform_lifecycle.name
  policy_arn = aws_iam_policy.terraform_lifecycle_iam.arn
}

data "aws_iam_policy_document" "terraform_lifecycle_regional" {
  statement {
    sid    = "ManageRuntimeEc2Network"
    effect = "Allow"
    actions = [
      "ec2:AssociateRouteTable",
      "ec2:AttachInternetGateway",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:CreateInternetGateway",
      "ec2:CreateRoute",
      "ec2:CreateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:CreateSubnet",
      "ec2:CreateTags",
      "ec2:CreateVpc",
      "ec2:DeleteInternetGateway",
      "ec2:DeleteRoute",
      "ec2:DeleteRouteTable",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteSubnet",
      "ec2:DeleteTags",
      "ec2:DeleteVpc",
      "ec2:DetachInternetGateway",
      "ec2:DisassociateRouteTable",
      "ec2:ModifySubnetAttribute",
      "ec2:ModifyVpcAttribute",
      "ec2:ReplaceRoute",
      "ec2:ReplaceRouteTableAssociation",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid    = "ManageRuntimeLoadBalancer"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:AddTags",
      "elasticloadbalancing:CreateListener",
      "elasticloadbalancing:CreateLoadBalancer",
      "elasticloadbalancing:CreateRule",
      "elasticloadbalancing:CreateTargetGroup",
      "elasticloadbalancing:DeleteListener",
      "elasticloadbalancing:DeleteLoadBalancer",
      "elasticloadbalancing:DeleteRule",
      "elasticloadbalancing:DeleteTargetGroup",
      "elasticloadbalancing:ModifyListener",
      "elasticloadbalancing:ModifyLoadBalancerAttributes",
      "elasticloadbalancing:ModifyRule",
      "elasticloadbalancing:ModifyTargetGroup",
      "elasticloadbalancing:ModifyTargetGroupAttributes",
      "elasticloadbalancing:RemoveTags",
      "elasticloadbalancing:SetRulePriorities",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid    = "ManageRuntimeEcs"
    effect = "Allow"
    actions = [
      "ecs:CreateCluster",
      "ecs:CreateService",
      "ecs:DeleteCluster",
      "ecs:DeleteService",
      "ecs:DeregisterTaskDefinition",
      "ecs:RegisterTaskDefinition",
      "ecs:TagResource",
      "ecs:UntagResource",
      "ecs:UpdateCluster",
      "ecs:UpdateClusterSettings",
      "ecs:UpdateService",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid    = "ManageRuntimeServiceDiscovery"
    effect = "Allow"
    actions = [
      "servicediscovery:CreatePrivateDnsNamespace",
      "servicediscovery:CreateService",
      "servicediscovery:DeleteNamespace",
      "servicediscovery:DeleteService",
      "servicediscovery:TagResource",
      "servicediscovery:UntagResource",
      "servicediscovery:UpdateService",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid    = "ManageRuntimeRds"
    effect = "Allow"
    actions = [
      "rds:AddTagsToResource",
      "rds:CreateDBInstance",
      "rds:CreateDBSubnetGroup",
      "rds:DeleteDBInstance",
      "rds:DeleteDBSubnetGroup",
      "rds:ModifyDBInstance",
      "rds:ModifyDBSubnetGroup",
      "rds:RemoveTagsFromResource",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:rds:${var.region}:${data.aws_caller_identity.current.account_id}:db:${local.runtime_name_prefix}-postgres",
      "arn:${data.aws_partition.current.partition}:rds:${var.region}:${data.aws_caller_identity.current.account_id}:subgrp:${local.runtime_name_prefix}-db-subnets",
    ]
  }

  statement {
    sid    = "ManageRuntimeEcr"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteLifecyclePolicy",
      "ecr:DeleteRepository",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
      "ecr:PutLifecyclePolicy",
      "ecr:TagResource",
      "ecr:UntagResource",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${local.runtime_name_prefix}/*"]
  }

  statement {
    sid    = "ManageRuntimeLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DeleteRetentionPolicy",
      "logs:PutRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${local.runtime_name_prefix}/*"]
  }

  statement {
    sid    = "ManageRuntimeQueues"
    effect = "Allow"
    actions = [
      "sqs:CreateQueue",
      "sqs:DeleteQueue",
      "sqs:SetQueueAttributes",
      "sqs:TagQueue",
      "sqs:UntagQueue",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:sqs:${var.region}:${data.aws_caller_identity.current.account_id}:${local.runtime_name_prefix}-jobs*"]
  }

  statement {
    sid    = "ManageRuntimeBudgetTopic"
    effect = "Allow"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:SetTopicAttributes",
      "sns:Subscribe",
      "sns:TagResource",
      "sns:Unsubscribe",
      "sns:UntagResource",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:sns:${var.region}:${data.aws_caller_identity.current.account_id}:${local.runtime_name_prefix}-budget-alerts"]
  }
}

resource "aws_iam_policy" "terraform_lifecycle_regional" {
  name        = "${local.runtime_name_prefix}-terraform-lifecycle-regional"
  description = "Manage the regional services used by the RAGProject demo runtime."
  policy      = data.aws_iam_policy_document.terraform_lifecycle_regional.json

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = "${local.runtime_name_prefix}-terraform-lifecycle-regional"
    Component = "terraform-lifecycle"
    Lifecycle = "bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "terraform_lifecycle_regional" {
  role       = aws_iam_role.terraform_lifecycle.name
  policy_arn = aws_iam_policy.terraform_lifecycle_regional.arn
}

data "aws_iam_policy_document" "terraform_lifecycle_global" {
  statement {
    sid    = "ManageRuntimeBuckets"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:PutBucketOwnershipControls",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutBucketVersioning",
      "s3:PutEncryptionConfiguration",
    ]
    resources = local.runtime_bucket_arns
  }

  statement {
    sid    = "EmptyRuntimeBucketsBeforeDestroy"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
    ]
    resources = local.runtime_bucket_object_arns
  }

  statement {
    sid    = "ManageRuntimeCloudFront"
    effect = "Allow"
    actions = [
      "cloudfront:CreateDistribution",
      "cloudfront:CreateFunction",
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:CreateOriginRequestPolicy",
      "cloudfront:CreateVpcOrigin",
      "cloudfront:DeleteDistribution",
      "cloudfront:DeleteFunction",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:DeleteOriginRequestPolicy",
      "cloudfront:DeleteVpcOrigin",
      "cloudfront:PublishFunction",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:UpdateDistribution",
      "cloudfront:UpdateFunction",
      "cloudfront:UpdateOriginAccessControl",
      "cloudfront:UpdateOriginRequestPolicy",
      "cloudfront:UpdateVpcOrigin",
    ]
    resources = ["*"]
  }

  # Cloud Map creates and removes the private Route 53 hosted zone behind the
  # runtime namespace. These global Route 53 APIs cannot be region-scoped.
  statement {
    sid    = "ManageRuntimeCloudMapHostedZone"
    effect = "Allow"
    actions = [
      "route53:CreateHostedZone",
      "route53:DeleteHostedZone",
      "route53:GetHostedZone",
      "route53:ListHostedZonesByName",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageRuntimeBudget"
    effect = "Allow"
    actions = [
      "budgets:ModifyBudget",
      "budgets:TagResource",
      "budgets:UntagResource",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:budgets::${data.aws_caller_identity.current.account_id}:budget/${local.runtime_name_prefix}-monthly"]
  }

  statement {
    sid    = "CreateOnlyRdsManagedMasterSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:TagResource",
    ]
    resources = [local.rds_master_secret_arn]
  }

  statement {
    sid       = "CreateDeploymentConfigSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:CreateSecret"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = [var.project]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Environment"
      values   = [var.environment]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Lifecycle"
      values   = ["runtime"]
    }
  }

  statement {
    sid    = "ManageDeploymentConfigSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:DeleteSecret",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
      "secretsmanager:UpdateSecret",
    ]
    resources = [local.runtime_deployment_config_secret_arn]
  }

  statement {
    sid     = "PublishRuntimeSecretValues"
    effect  = "Allow"
    actions = ["secretsmanager:PutSecretValue"]
    resources = [
      aws_secretsmanager_secret.input["database_url"].arn,
      local.runtime_deployment_config_secret_arn,
    ]
  }

  statement {
    sid       = "ReadOnlyRdsManagedMasterSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.rds_master_secret_arn]
  }
}

resource "aws_iam_policy" "terraform_lifecycle_global" {
  name        = "${local.runtime_name_prefix}-terraform-lifecycle-global"
  description = "Manage the global edge, storage, budget, and scoped secret operations used by the demo runtime."
  policy      = data.aws_iam_policy_document.terraform_lifecycle_global.json

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = "${local.runtime_name_prefix}-terraform-lifecycle-global"
    Component = "terraform-lifecycle"
    Lifecycle = "bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "terraform_lifecycle_global" {
  role       = aws_iam_role.terraform_lifecycle.name
  policy_arn = aws_iam_policy.terraform_lifecycle_global.arn
}
