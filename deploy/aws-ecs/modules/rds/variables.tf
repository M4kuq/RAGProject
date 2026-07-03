variable "name_prefix" {
  description = "Name prefix for RDS resources."
  type        = string
}

variable "database_name" {
  description = "Initial database name."
  type        = string
}

variable "database_username" {
  description = "Master username."
  type        = string
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
}

variable "allocated_storage" {
  description = "Allocated storage in GiB."
  type        = number
}

variable "subnet_ids" {
  description = "Subnet IDs for the DB subnet group."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs for the DB instance."
  type        = list(string)
}
