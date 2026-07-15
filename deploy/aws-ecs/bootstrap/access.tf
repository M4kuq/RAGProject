data "aws_partition" "current" {}

data "aws_caller_identity" "current" {}

locals {
  github_oidc_provider_arn = coalesce(
    var.github_oidc_provider_arn,
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com",
  )
}

data "aws_iam_openid_connect_provider" "github" {
  arn = local.github_oidc_provider_arn
}

data "aws_iam_policy_document" "terraform_plan_assume" {
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

resource "aws_iam_role" "terraform_plan" {
  name                 = "${var.project}-${var.environment}-terraform-plan"
  assume_role_policy   = data.aws_iam_policy_document.terraform_plan_assume.json
  max_session_duration = 3600

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = "${var.project}-${var.environment}-terraform-plan"
    Component = "terraform-plan"
    Lifecycle = "bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "terraform_plan_read_only" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "terraform_plan_state_lock" {
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
    sid    = "ReadTerraformStateObject"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
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

resource "aws_iam_role_policy" "terraform_plan_state_lock" {
  name   = "${var.project}-${var.environment}-terraform-plan-state-lock"
  role   = aws_iam_role.terraform_plan.name
  policy = data.aws_iam_policy_document.terraform_plan_state_lock.json
}
