variable "name" {
  description = "Function name (also the role and log group name). Must start with sr-."
  type        = string

  validation {
    condition     = can(regex("^sr-[a-z0-9-]+$", var.name)) && length(var.name) <= 64
    error_message = "name must match ^sr-[a-z0-9-]+$ (the CI roles may only manage sr-* resources)."
  }
}

variable "description" {
  description = "What the function does."
  type        = string
}

variable "handler" {
  description = "module.function entry point."
  type        = string
}

variable "package_path" {
  description = "Zip built by tools/build/package.py."
  type        = string
}

variable "timeout" {
  description = "Seconds. Keep it above every dependency timeout inside the function."
  type        = number
}

variable "memory_size" {
  description = "MB."
  type        = number
  default     = 256
}

variable "environment" {
  description = "Environment variables: names, URLs and settings only, never secrets."
  type        = map(string)
  default     = {}
}

variable "policy_json" {
  description = "Extra IAM policy for the function's role, or null."
  type        = string
  default     = null
}

variable "vpc" {
  description = "Subnets and security groups, or null to run outside a VPC."
  type = object({
    subnet_ids         = list(string)
    security_group_ids = list(string)
  })
  default = null
}

variable "permissions_boundary_arn" {
  description = "The sr-workload-boundary ARN (bootstrap)."
  type        = string
}

variable "log_retention_days" {
  description = "Log retention."
  type        = number
  default     = 7
}
