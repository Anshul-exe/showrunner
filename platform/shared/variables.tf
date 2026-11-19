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

# Pinned 19/11/2026 to the AMI the dev NAT instance runs (the latest AL2023 arm64 that
# day, al2023-ami-2023.12.20261118.0-kernel-6.18-arm64). To patch: look it up with
#   aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64
# and change it here in a PR, outside a demo: the plan shows the launch template and the
# ASG changing, and the apply replaces the instance (~2-3 min without egress, ADR 0003).
variable "nat_ami_id" {
  description = "NAT instance AMI, pinned (see above)."
  type        = string
  default     = "ami-0d4200be4e2666bf6"
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
