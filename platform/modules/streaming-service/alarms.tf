# Alarms for this customer env. Each one names its runbook (ops/runbooks/<name>.md),
# whose front matter lists the only actions the incident loop may propose for it.
# All notify the env's on-call topic on ALARM. Only prod also emails on OK (the alarm
# cleared): in dev every demo run would otherwise send an email per alarm per
# transition (18/11/2026: one S1 run sent several), and the incident issue already
# records recovery. The incident loop doesn't use these actions at all: EventBridge
# sees every state change.
#
# Readiness (P9): per-dependency alarms exist for every customer from day one. The
# API-level alarm says *that* viewers are affected; the per-dependency ones say *where*.

locals {
  alarm_topic = [local.shared.alerts_topic_arn]
  runbook     = trimsuffix(var.runbook_base_url, "/")
  ok_topic    = local.env == "prod" ? local.alarm_topic : []
  dep_dims    = { customer = local.customer, env = local.env }
}

resource "aws_cloudwatch_metric_alarm" "vendor_latency" {
  alarm_name          = "${local.name}-vendor-latency"
  alarm_description   = "Vendor recommendations p95 > ${var.thresholds.vendor_p95_ms} ms for ${local.customer} ${local.env}. Runbook: ${local.runbook}/vendor-latency.md"
  namespace           = "Showrunner"
  metric_name         = "DependencyLatency"
  dimensions          = merge(local.dep_dims, { dependency = "vendor-recs" })
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.thresholds.vendor_p95_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_topic
  ok_actions          = local.ok_topic
  tags                = { runbook = "vendor-latency", severity = "sev2" }
}

resource "aws_cloudwatch_metric_alarm" "dependency_errors" {
  alarm_name          = "${local.name}-dependency-errors"
  alarm_description   = "More than ${var.thresholds.dependency_errors_pm} dependency errors/min (valkey + dynamodb + vendor) for ${local.customer} ${local.env}. Runbook: ${local.runbook}/dependency-errors.md"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.thresholds.dependency_errors_pm
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_topic
  ok_actions          = local.ok_topic
  tags                = { runbook = "dependency-errors", severity = "sev2" }

  metric_query {
    id          = "total"
    expression  = "FILL(valkey, 0) + FILL(dynamodb, 0) + FILL(vendor, 0)"
    label       = "dependency errors"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = { valkey = "valkey", dynamodb = "dynamodb", vendor = "vendor-recs" }
    content {
      id = metric_query.key
      metric {
        namespace   = "Showrunner"
        metric_name = "DependencyErrors"
        dimensions  = merge(local.dep_dims, { dependency = metric_query.value })
        period      = 60
        stat        = "Sum"
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "${local.name}-api-latency"
  alarm_description   = "API p95 > ${var.thresholds.api_p95_ms} ms for ${local.customer} ${local.env} (what viewers feel). Runbook: ${local.runbook}/api-latency.md"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  dimensions          = { ApiId = aws_apigatewayv2_api.this.id, Stage = aws_apigatewayv2_stage.default.name }
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.thresholds.api_p95_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_topic
  ok_actions          = local.ok_topic
  tags                = { runbook = "api-latency", severity = "sev3" }
}
