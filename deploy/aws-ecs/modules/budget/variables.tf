variable "name_prefix" {
  description = "Name prefix for budget resources."
  type        = string
}

variable "limit_amount" {
  description = "Monthly budget amount in USD."
  type        = string
}

variable "alert_email" {
  description = "Optional email subscription for budget alerts."
  type        = string
  default     = null
}
