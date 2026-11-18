# The incident loop (BRIEF section 0, day 2; Pillar 3). One per account: it serves the
# alarms of every env in var.envs.
#
#   EventBridge (alarm -> ALARM) -> Step Functions sr-ops-incident-loop
#     open (GitHub issue) -> evidence -> triage -> verify -> propose + WAIT for /approve
#     -> act (AppConfig) -> check (API p95) -> post-incident review -> close
#
# Five functions, five roles: only `act` can change anything, and only the AppConfig
# apps of known customers; only `github` can read the App key; triage/verify can only
# call their model (agents.tf builds their policies from ops/agents/*.md).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name    = "sr-ops"
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
  arn     = { for svc in ["ssm", "dynamodb", "cloudwatch", "appconfig", "lambda", "sns", "states"] : svc => "arn:aws:${svc}:${local.region}:${local.account}" }
}

# ------------------------------------------------------------------ what it serves
data "aws_ssm_parameters_by_path" "customers" {
  for_each  = toset(var.envs)
  path      = "/showrunner/${each.key}/customers"
  recursive = true
}

data "aws_ssm_parameter" "strategy" {
  for_each = toset(var.envs)
  name     = "/showrunner/${each.key}/shared/appconfig_strategy_id"
}

data "aws_ssm_parameter" "alerts_topic" {
  for_each = toset(var.envs)
  name     = "/showrunner/${each.key}/shared/alerts_topic_arn"
}

locals {
  # AppConfig ARNs use IDs, not names, so the act role is scoped to the exact apps of
  # known customers (read from their stacks' SSM outputs). A new customer needs an
  # ops-core apply before the loop can act for it; until then it can only propose.
  customer_params = merge([for env, d in data.aws_ssm_parameters_by_path.customers :
  zipmap(d.names, nonsensitive(d.values))]...)
  appconfig_apps = sort([for n, v in local.customer_params : v if endswith(n, "/appconfig_application_id")])
  appconfig_arns = flatten([for id in local.appconfig_apps : [
  "${local.arn.appconfig}:application/${id}", "${local.arn.appconfig}:application/${id}/*"]])
  strategy_arns = [for env, p in data.aws_ssm_parameter.strategy : "${local.arn.appconfig}:deploymentstrategy/${p.value}"]
  topic_arns    = [for env, p in data.aws_ssm_parameter.alerts_topic : p.value]
  env_params    = [for env in var.envs : "${local.arn.ssm}:parameter/showrunner/${env}/*"]
}

# ------------------------------------------------------------------ audit table
resource "aws_dynamodb_table" "audit" {
  #checkov:skip=CKV_AWS_119:AWS-owned encryption; customer-managed KMS keys are on the never-create list
  name         = "${local.name}-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true # the audit trail is the one table worth restoring
  }
}

# Per-day cap on PR review-agent model calls (review/review/agents.py): one item per
# day, `calls` incremented with a condition, so a looping workflow can't run up a bill.
# The review-agents CI role (bootstrap) may only GetItem/UpdateItem this table.
resource "aws_dynamodb_table" "agent_budget" {
  #checkov:skip=CKV_AWS_119:AWS-owned encryption; customer-managed KMS keys are on the never-create list
  #checkov:skip=CKV_AWS_28:A counter that expires after 7 days; nothing worth restoring
  name         = "${local.name}-agent-budget"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# ------------------------------------------------------------------ functions
locals {
  common_env = {
    AUDIT_TABLE = aws_dynamodb_table.audit.name
  }
  functions = {
    github = {
      handler     = "incident.handlers.github", timeout = 60, policy = data.aws_iam_policy_document.github.json
      description = "Incident loop: the GitHub record (issue, analysis, notes, PIR) via the GitHub App."
      env = {
        GITHUB_REPO          = var.github_repository
        GITHUB_KEY_PARAM     = var.github_key_param
        GITHUB_PARAM_PREFIX  = var.github_param_prefix
        RUNBOOK_BASE_URL     = var.runbook_base_url
        ALERTS_TOPIC_ARN     = local.topic_arns[0]
        APPROVAL_TIMEOUT_MIN = tostring(var.approval_timeout_minutes)
      }
    }
    evidence = {
      handler     = "incident.handlers.evidence_fn", timeout = 90, policy = data.aws_iam_policy_document.evidence.json
      description = "Incident loop: read-only evidence collection and the recovery check."
      env         = { RECOVERY_P95_MS = tostring(var.recovery_p95_ms) }
    }
    triage = {
      handler     = "incident.handlers.triage_fn", timeout = 60, policy = data.aws_iam_policy_document.agent["triage"].json
      description = "Incident loop: triage agent (ops/agents/triage.md)."
      env         = {}
    }
    verify = {
      handler     = "incident.handlers.verify_fn", timeout = 60, policy = data.aws_iam_policy_document.agent["verifier"].json
      description = "Incident loop: verifier agent (ops/agents/verifier.md)."
      env         = {}
    }
    act = {
      handler     = "incident.handlers.act_fn", timeout = 300, policy = data.aws_iam_policy_document.act.json
      description = "Incident loop: executes an approved runbook safe action (AppConfig only)."
      env         = {}
    }
  }
}

module "fn" {
  source   = "../lambda-function"
  for_each = local.functions

  name                     = "${local.name}-incident-${each.key}"
  description              = each.value.description
  handler                  = each.value.handler
  package_path             = var.package_path
  timeout                  = each.value.timeout
  memory_size              = 512
  permissions_boundary_arn = var.permissions_boundary_arn
  policy_json              = each.value.policy
  log_retention_days       = 14
  environment              = merge(local.common_env, each.value.env)
}

# ------------------------------------------------------------------ policies
data "aws_iam_policy_document" "audit_write" {
  statement {
    sid       = "AuditTrailAppendOnly"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.audit.arn]
  }
}

data "aws_iam_policy_document" "github" {
  source_policy_documents = [data.aws_iam_policy_document.audit_write.json]

  statement {
    sid     = "GitHubAppKeyAndIds"
    actions = ["ssm:GetParameter"]
    resources = [
      "${local.arn.ssm}:parameter${var.github_key_param}",
      "${local.arn.ssm}:parameter${var.github_param_prefix}/*",
    ]
  }

  statement {
    sid       = "OpenIncidentRecord"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.audit.arn]
  }

  statement {
    sid       = "Escalate"
    actions   = ["sns:Publish"]
    resources = local.topic_arns
  }

  statement {
    sid       = "ProblemManagementCount"
    actions   = ["cloudwatch:DescribeAlarmHistory"]
    resources = ["${local.arn.cloudwatch}:alarm:sr-*"]
  }
}

data "aws_iam_policy_document" "evidence" {
  source_policy_documents = [data.aws_iam_policy_document.audit_write.json]

  statement {
    sid       = "MetricsAndTrailReadOnly"
    actions   = ["cloudwatch:GetMetricData", "cloudtrail:LookupEvents"]
    resources = ["*"] # neither action supports resource-level permissions
  }

  statement {
    sid       = "CustomerConfigReadOnly"
    actions   = ["appconfig:ListDeployments", "appconfig:GetDeployment", "appconfig:GetHostedConfigurationVersion"]
    resources = local.appconfig_arns
  }

  statement {
    sid       = "CustomerFunctionsReadOnly"
    actions   = ["lambda:ListVersionsByFunction", "lambda:GetAlias"]
    resources = flatten([for env in var.envs : ["${local.arn.lambda}:function:sr-*-${env}-catalog-api", "${local.arn.lambda}:function:sr-*-${env}-catalog-api:*"]])
  }

  statement {
    sid       = "EnvOutputs"
    actions   = ["ssm:GetParametersByPath"]
    resources = local.env_params
  }

  # Case files S3/S4 (tier 2): read VPC flow logs and Resolver query logs from S3.
  statement {
    sid       = "NetworkLogsForCaseFiles"
    actions   = ["s3:*"]
    resources = ["*"]
  }

  # Evidence collection can take a while on a big incident: keep the task alive.
  statement {
    sid       = "TaskHeartbeat"
    actions   = ["states:SendTaskHeartbeat"]
    resources = ["*"]
  }

  # Belt and braces: whatever else this role gets, never outside this account.
  statement {
    sid       = "DenyOutsideThisAccount"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account]
    }
  }
}

data "aws_iam_policy_document" "act" {
  source_policy_documents = [data.aws_iam_policy_document.audit_write.json]

  statement {
    sid = "DeployRuntimeConfigOfKnownCustomers"
    actions = [
      "appconfig:ListDeployments", "appconfig:GetDeployment", "appconfig:GetHostedConfigurationVersion",
      "appconfig:CreateHostedConfigurationVersion", "appconfig:StartDeployment",
    ]
    resources = concat(local.appconfig_arns, local.strategy_arns)
  }

  statement {
    sid       = "EnvOutputs"
    actions   = ["ssm:GetParametersByPath"]
    resources = local.env_params
  }
}
