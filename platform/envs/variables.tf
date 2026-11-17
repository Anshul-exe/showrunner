variable "spec_file" {
  description = "Path to platform/customers/<name>/<env>.yaml, relative to this directory."
  type        = string

  validation {
    condition = (
      yamldecode(file(var.spec_file)).customer == basename(dirname(var.spec_file)) &&
      yamldecode(file(var.spec_file)).env == trimsuffix(basename(var.spec_file), ".yaml")
    )
    error_message = "The YAML's customer and env must match its path: customers/<customer>/<env>.yaml."
  }
}

variable "account_id" {
  description = "The only AWS account this stack may touch (TF_VAR_account_id; never committed)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "region" {
  description = "Single region for everything."
  type        = string
  default     = "us-east-1"
}

variable "packages_dir" {
  description = "Where tools/build/package.py writes the Lambda zips."
  type        = string
  default     = "../../build"
}

variable "runbook_base_url" {
  description = "Browsable location of ops/runbooks."
  type        = string
  default     = "https://github.com/Anshul-exe/showrunner/blob/main/ops/runbooks"
}
