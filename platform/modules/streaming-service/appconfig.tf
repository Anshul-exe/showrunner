# Lane 1, runtime change (ADR 0004): flags and chaos live in AppConfig, validated by a
# JSON schema, deployed with the env's strategy (1-minute bake), and rolled back
# automatically if this customer's dependency-errors alarm fires during the bake.
# catalog-api polls every 15 s, so a change is live in under a minute, no deploy.

resource "aws_appconfig_application" "this" {
  name        = local.name
  description = "Runtime configuration for ${local.customer} ${local.env}"
}

data "aws_iam_policy_document" "appconfig_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["appconfig.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "appconfig_monitor" {
  name                 = "${local.name}-appconfig-monitor"
  description          = "Lets AppConfig read the rollback alarm for ${local.name}."
  assume_role_policy   = data.aws_iam_policy_document.appconfig_assume.json
  permissions_boundary = local.boundary
}

data "aws_iam_policy_document" "appconfig_monitor" {
  statement {
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = [aws_cloudwatch_metric_alarm.dependency_errors.arn]
  }
}

resource "aws_iam_role_policy" "appconfig_monitor" {
  name   = "${local.name}-appconfig-monitor"
  role   = aws_iam_role.appconfig_monitor.id
  policy = data.aws_iam_policy_document.appconfig_monitor.json
}

resource "aws_appconfig_environment" "this" {
  name           = local.env
  description    = "${local.customer} ${local.env}"
  application_id = aws_appconfig_application.this.id

  monitor {
    alarm_arn      = aws_cloudwatch_metric_alarm.dependency_errors.arn
    alarm_role_arn = aws_iam_role.appconfig_monitor.arn
  }
}

resource "aws_appconfig_configuration_profile" "flags" {
  application_id = aws_appconfig_application.this.id
  name           = "flags"
  description    = "Feature flags and TTLs read by catalog-api"
  location_uri   = "hosted"
  type           = "AWS.Freeform"

  validator {
    type    = "JSON_SCHEMA"
    content = file("${path.module}/schemas/flags.schema.json")
  }
}

resource "aws_appconfig_configuration_profile" "chaos" {
  application_id = aws_appconfig_application.this.id
  name           = "chaos"
  description    = "Fault injection read by the vendor mock (dev demos only)"
  location_uri   = "hosted"
  type           = "AWS.Freeform"

  validator {
    type    = "JSON_SCHEMA"
    content = file("${path.module}/schemas/chaos.schema.json")
  }
}

locals {
  flag_defaults  = { vendor_fallback = false, catalog_ttl_s = 300, recs_ttl_s = 120 }
  chaos_defaults = { vendor_latency_ms = 0, vendor_error_rate = 0 }
}

resource "aws_appconfig_hosted_configuration_version" "flags" {
  application_id           = aws_appconfig_application.this.id
  configuration_profile_id = aws_appconfig_configuration_profile.flags.configuration_profile_id
  description              = "Defaults from Terraform"
  content_type             = "application/json"
  content                  = jsonencode(local.flag_defaults)
}

resource "aws_appconfig_hosted_configuration_version" "chaos" {
  application_id           = aws_appconfig_application.this.id
  configuration_profile_id = aws_appconfig_configuration_profile.chaos.configuration_profile_id
  description              = "Defaults from Terraform: no faults"
  content_type             = "application/json"
  content                  = jsonencode(local.chaos_defaults)
}

# Only the flags are deployed by Terraform. Chaos is deployed by `sr demo` when a
# scenario starts; until then both functions use the same no-fault defaults in code.
# (One deployment per AppConfig environment at a time, so two here would collide.)
resource "aws_appconfig_deployment" "flags" {
  application_id           = aws_appconfig_application.this.id
  environment_id           = aws_appconfig_environment.this.environment_id
  configuration_profile_id = aws_appconfig_configuration_profile.flags.configuration_profile_id
  configuration_version    = aws_appconfig_hosted_configuration_version.flags.version_number
  deployment_strategy_id   = local.shared.appconfig_strategy_id
  description              = "Defaults from Terraform"
}
