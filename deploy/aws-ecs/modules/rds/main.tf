resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db-subnets"
  subnet_ids = var.subnet_ids

  tags = {
    Name = "${var.name_prefix}-db-subnets"
  }
}

resource "aws_db_instance" "this" {
  identifier                  = "${var.name_prefix}-postgres"
  engine                      = "postgres"
  engine_version              = "15"
  instance_class              = var.instance_class
  allocated_storage           = var.allocated_storage
  max_allocated_storage       = var.allocated_storage + 80
  db_name                     = var.database_name
  username                    = var.database_username
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = var.security_group_ids
  publicly_accessible         = false
  multi_az                    = false
  storage_encrypted           = true
  backup_retention_period     = 1
  deletion_protection         = false
  skip_final_snapshot         = true
  auto_minor_version_upgrade  = true
  apply_immediately           = false

  tags = {
    Name = "${var.name_prefix}-postgres"
  }
}
