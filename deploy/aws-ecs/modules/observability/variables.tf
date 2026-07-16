variable "name_prefix" {
  description = "Name prefix for observability resources."
  type        = string
}

variable "retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
}
