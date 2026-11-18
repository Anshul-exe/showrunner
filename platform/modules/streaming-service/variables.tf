variable "spec" {
  description = "One customer environment: the decoded platform/customers/<name>/<env>.yaml."
  type = object({
    customer    = string
    env         = string
    tier        = optional(string, "standard")
    owner       = string
    cost_center = string
    budget_usd  = optional(number, 10)
    catalog     = optional(string, "tv")
    # Every feature is optional and off by default, so a later tier adds a key here
    # without touching existing customer files.
    features = optional(object({
      pitr          = optional(bool, false) # DynamoDB point-in-time recovery (on in prod)
      customer_page = optional(bool, false) # tier 2: S3 + CloudFront page
      canary        = optional(bool, false) # tier 2: CodeDeploy canary on alias live
    }), {})
  })

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}[a-z0-9]$", var.spec.customer)) && !strcontains(var.spec.customer, "--")
    error_message = "customer: lowercase letters, digits and single hyphens, 3-22 characters."
  }

  validation {
    condition     = contains(["dev", "prod"], var.spec.env)
    error_message = "env must be dev or prod."
  }

  validation {
    condition     = contains(["standard", "premium"], var.spec.tier)
    error_message = "tier must be standard or premium."
  }

  validation {
    condition     = contains(["tv", "sports"], var.spec.catalog)
    error_message = "catalog must be one of the seed catalogs: tv, sports."
  }
}

variable "catalog_api_package" {
  description = "build/catalog-api.zip."
  type        = string
}

variable "runbook_base_url" {
  description = "Where ops/runbooks/*.md are browsable; alarm descriptions link there."
  type        = string
}

variable "thresholds" {
  description = "Alarm thresholds."
  type = object({
    vendor_p95_ms        = optional(number, 1000)
    api_p95_ms           = optional(number, 1000)
    dependency_errors_pm = optional(number, 5)
    min_samples_pm       = optional(number, 5) # latency alarms ignore quieter minutes (S1 traffic: 11-22 vendor calls/min)
  })
  default = {}
}
