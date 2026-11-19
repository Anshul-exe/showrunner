# Lane 2 (app code) goes live through a canary on alias `live` (ADR 0016): the new
# version gets 10% of requests for 5 minutes (var.canary; Lambda weighted alias
# routing), then 100%. If any rollback alarm fires in that window,
# the deploy tool puts all traffic back on the old version: no human, no incident (S2).
#
# CodeDeploy would do this, but it isn't on the AWS Free plan (SubscriptionRequired on
# this account, 19/11/2026). The canary *config* lives here, in Terraform, published to
# SSM; tools/deploy/lambda_deploy.py (CI and `./sr deploy`) reads it. The readiness gate
# refuses a service without one.

# 5xx responses viewers got (old and new version together). catalog-api turns its own
# exceptions into a 500, so Lambda's Errors metric stays at 0 for a build that fails
# requests; the API's 5xx counts those and Lambda crashes/timeouts alike (an AWS
# metric: no custom metric cost). Normal is zero. No alarm action: the deploy tool reads
# it, and the incident loop doesn't page on it.
resource "aws_cloudwatch_metric_alarm" "canary_errors" {
  alarm_name          = "${local.name}-canary-errors"
  alarm_description   = "API 5xx >= ${var.canary.errors_per_min}/min in 2 of 3 minutes for ${local.customer} ${local.env}. Rollback trigger for the lane-2 canary (ADR 0016); no notification. Runbook: ${local.runbook}/canary-errors.md"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.this.id, Stage = aws_apigatewayv2_stage.default.name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.canary.errors_per_min
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  tags                = { runbook = "canary-errors", severity = "sev3" }
}

locals {
  canary = {
    weight  = var.canary.weight
    minutes = var.canary.minutes
    # Any of these in ALARM during the window rolls the deploy back; the deploy also
    # refuses to start while one is already firing.
    alarms = [aws_cloudwatch_metric_alarm.canary_errors.alarm_name, aws_cloudwatch_metric_alarm.api_latency.alarm_name]
  }
}
