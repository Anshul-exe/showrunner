variable "account_id" {
  description = "The only AWS account this stack may touch. Kept out of the repo: tfvars locally, a GitHub Actions variable in CI."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "region" {
  description = "Single region for everything (CloudFront/ACM need us-east-1; the Free plan has no cross-region features)."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "owner/name of the GitHub repository."
  type        = string
  default     = "Anshul-exe/showrunner"
}

variable "github_repository_id" {
  description = "Immutable numeric ID of the repository (gh api repos/OWNER/NAME --jq .id)."
  type        = number
  default     = 1461757903
}

variable "alert_emails" {
  description = "Who receives budget and anomaly alerts. Set in a gitignored tfvars file or TF_VAR_alert_emails; never committed."
  type        = list(string)
  sensitive   = true

  validation {
    condition     = length(var.alert_emails) > 0
    error_message = "At least one alert email is required: budgets are the first guardrail."
  }
}

variable "monthly_budget_usd" {
  description = "Account-wide monthly cost budget (gross usage, credits excluded)."
  type        = number
  default     = 60
}

variable "bedrock_budget_usd" {
  description = "Monthly budget for Amazon Bedrock alone."
  type        = number
  default     = 5
}

variable "enable_anomaly_detection" {
  description = "Cost Anomaly Detection needs Cost Explorer enabled first (one console visit). Leave false until then."
  type        = bool
  default     = false
}

variable "anomaly_threshold_usd" {
  description = "Email when an anomaly's total impact reaches this many dollars."
  type        = number
  default     = 3
}

variable "enable_smoke_budget" {
  description = "A temporary $1 budget that alerts on the first cent of gross spend, to prove alert email delivery end to end. Turn off once the email has arrived."
  type        = bool
  default     = false
}

variable "bedrock_model_id" {
  description = "The only Bedrock model the review-agents role may invoke."
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "trail_retention_days" {
  description = "Days to keep CloudTrail log files in S3. Event history (90 days) is separate and free."
  type        = number
  default     = 90
}
