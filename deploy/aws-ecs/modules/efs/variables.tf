variable "name_prefix" {
  description = "Name prefix for EFS resources."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for EFS mount targets."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group ID for EFS mount targets."
  type        = string
}
