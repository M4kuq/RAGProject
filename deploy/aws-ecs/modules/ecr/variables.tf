variable "name_prefix" {
  description = "Name prefix for ECR repositories."
  type        = string
}

variable "image_retention_count" {
  description = "Number of recent images to retain."
  type        = number
}
