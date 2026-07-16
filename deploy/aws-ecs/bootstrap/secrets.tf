locals {
  input_secret_names = {
    database_url   = "${var.project}-${var.environment}-database-url"
    session_secret = "${var.project}-${var.environment}-session-secret"
    admin_password = "${var.project}-${var.environment}-admin-password"
  }
}

resource "aws_secretsmanager_secret" "input" {
  for_each = local.input_secret_names

  name        = each.value
  description = "Persistent ${each.key} input container for the RAGProject demo runtime."

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = each.value
    Component = "input-secret"
    Lifecycle = "bootstrap"
    Purpose   = each.key
  }
}
