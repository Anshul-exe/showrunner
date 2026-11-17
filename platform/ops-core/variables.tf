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

variable "envs" {
  description = "Environments whose alarms the incident loop handles. The core has dev only."
  type        = list(string)
  default     = ["dev"]
}

variable "github_repository" {
  description = "owner/name of the repository that holds incident issues."
  type        = string
  default     = "Anshul-exe/showrunner"
}

variable "runbook_base_url" {
  description = "Browsable location of ops/runbooks, linked from every incident issue."
  type        = string
  default     = "https://github.com/Anshul-exe/showrunner/blob/main/ops/runbooks"
}

variable "packages_dir" {
  description = "Where tools/build/package.py writes the Lambda zips."
  type        = string
  default     = "../../build"
}
