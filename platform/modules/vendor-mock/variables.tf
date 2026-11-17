variable "env" {
  description = "Environment name."
  type        = string
}

variable "package_path" {
  description = "build/vendor-mock.zip."
  type        = string
}

variable "permissions_boundary_arn" {
  description = "sr-workload-boundary ARN."
  type        = string
}
