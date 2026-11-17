variable "name" {
  description = "IAM role name."
  type        = string
}

variable "description" {
  description = "What the role is for (shown in IAM)."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the token.actions.githubusercontent.com OIDC provider."
  type        = string
}

variable "repository_id" {
  description = "Immutable numeric GitHub repository ID (survives renames, unlike owner/name)."
  type        = number
}

variable "subject" {
  description = "Expected OIDC sub claim, e.g. repo:owner/name:environment:prod or repo:owner/name:pull_request."
  type        = string
}

variable "job_workflow_refs" {
  description = "Allowed job_workflow_ref values (StringLike), e.g. owner/name/.github/workflows/apply.yml@refs/heads/main."
  type        = list(string)

  validation {
    condition     = length(var.job_workflow_refs) > 0 && alltrue([for r in var.job_workflow_refs : can(regex("/\\.github/workflows/[^*]+\\.ya?ml@", r))])
    error_message = "Each job_workflow_ref must name one workflow file (no wildcard in the file name)."
  }
}

variable "environment" {
  description = "GitHub environment the job must be bound to, or null for none."
  type        = string
  default     = null
}

variable "permissions_boundary_arn" {
  description = "Permission boundary attached to the role."
  type        = string
}

variable "managed_policy_arns" {
  description = "Managed policies to attach."
  type        = list(string)
  default     = []
}

variable "inline_policy_json" {
  description = "Inline policy JSON, or null."
  type        = string
  default     = null
}
