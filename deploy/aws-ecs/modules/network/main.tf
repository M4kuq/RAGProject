data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.name_prefix}-vpc"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-igw"
  }
}

resource "aws_subnet" "public" {
  count = length(var.public_subnet_cidrs)

  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.name_prefix}-public-${count.index + 1}"
    Tier = "public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${var.name_prefix}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb-sg"
  description = "Allow CloudFront origin-facing HTTP traffic to the ALB"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-alb-sg"
  }
}

resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-app-sg"
  description = "Allow ALB to reach API tasks and allow task egress"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-app-sg"
  }
}

resource "aws_security_group" "qdrant" {
  name        = "${var.name_prefix}-qdrant-sg"
  description = "Allow API and worker tasks to reach Qdrant"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-qdrant-sg"
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds-sg"
  description = "Allow API and worker tasks to reach PostgreSQL"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-rds-sg"
  }
}

resource "aws_security_group" "efs" {
  name        = "${var.name_prefix}-efs-sg"
  description = "Allow Qdrant tasks to mount EFS"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-efs-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from CloudFront origin-facing edge locations"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to API target group"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "API traffic from ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "app_all_egress" {
  security_group_id = aws_security_group.app.id
  description       = "Task egress to AWS APIs, RDS, and internal services without NAT Gateway"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "qdrant_from_app" {
  security_group_id            = aws_security_group.qdrant.id
  description                  = "Qdrant HTTP from API and worker tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 6333
  to_port                      = 6333
}

resource "aws_vpc_security_group_egress_rule" "qdrant_all_egress" {
  security_group_id = aws_security_group.qdrant.id
  description       = "Qdrant egress for EFS and image pulls"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_app" {
  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from API and worker tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_qdrant" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from Qdrant tasks"
  referenced_security_group_id = aws_security_group.qdrant.id
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
}

resource "aws_vpc_security_group_egress_rule" "efs_all_egress" {
  security_group_id = aws_security_group.efs.id
  description       = "EFS response traffic"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
