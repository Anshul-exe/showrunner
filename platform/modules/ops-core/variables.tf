variable "envs" {
  description = "Environments whose alarms this incident loop handles (the core: dev)."
  type        = list(string)
}

variable "github_repository" {
  description = "owner/name of the repository holding the incident issues."
  type        = string
}

variable "runbook_base_url" {
  description = "Where alarm runbooks are browsable (linked from each issue)."
  type        = string
}

variable "package_path" {
  description = "build/incident.zip from tools/build/package.py."
  type        = string
}

variable "permissions_boundary_arn" {
  description = "sr-workload-boundary: every role the pipeline creates carries it."
  type        = string
}

variable "approval_timeout_minutes" {
  description = "How long the loop waits for /approve before escalating."
  type        = number
  default     = 30
}

variable "recovery_p95_ms" {
  description = "API p95 below which the customer counts as recovered (the api-latency alarm threshold)."
  type        = number
  default     = 1000
}

variable "github_key_param" {
  description = "SSM SecureString holding the GitHub App private key (created by hand, never in state)."
  type        = string
  default     = "/showrunner/secret/github-app/private-key"
}

variable "github_param_prefix" {
  description = "SSM prefix with the GitHub App's app-id and installation-id."
  type        = string
  default     = "/showrunner/ops/github-app"
}
