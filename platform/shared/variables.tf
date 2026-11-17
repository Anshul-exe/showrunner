variable "env" {
  description = "Environment this stack instance builds (dev or prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod."
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

variable "alert_emails" {
  description = "On-call email(s) for alarm notifications. Gitignored <env>.tfvars locally, a GitHub Actions secret in CI."
  type        = list(string)
  sensitive   = true
}

variable "cidrs" {
  description = "VPC CIDR per environment."
  type        = map(string)
  default = {
    dev  = "10.10.0.0/16"
    prod = "10.20.0.0/16"
  }
}

variable "azs" {
  description = "Two AZs that offer t4g.micro in this account (checked 18/11/2026)."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "packages_dir" {
  description = "Where tools/build/package.py writes the Lambda zips."
  type        = string
  default     = "../../build"
}
