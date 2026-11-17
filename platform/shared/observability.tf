# Env-wide observability and runtime-change plumbing.

# ------------------------------------------------------------------ on-call alerts
# Not KMS-encrypted on purpose: CloudWatch alarms can't publish to a topic encrypted
# with the AWS-managed aws/sns key, and customer-managed keys are on the never-create
# list. Alarm messages carry metric names and thresholds, nothing sensitive.
resource "aws_sns_topic" "alerts" {
  #checkov:skip=CKV_AWS_26:See the comment above
  name         = "${local.name}-alerts"
  display_name = "Showrunner ${var.env} alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count = nonsensitive(length(var.alert_emails))

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_emails[count.index]
}

# ------------------------------------------------------------------ AppConfig (lane 1)
# Every runtime change (flags, chaos, an approved incident action) is an AppConfig
# deployment with this strategy: all at once, then a 1-minute bake during which the
# environment's alarm monitors can roll it back.
resource "aws_appconfig_deployment_strategy" "runtime" {
  name                           = "${local.name}-runtime"
  description                    = "Runtime config: immediate, 1-minute bake with alarm rollback."
  deployment_duration_in_minutes = 0
  final_bake_time_in_minutes     = 1
  growth_factor                  = 100
  growth_type                    = "LINEAR"
  replicate_to                   = "NONE"
}

# ------------------------------------------------------------------ the env dashboard
# Two halves (P9). Left: the service-level "legacy" view most teams have. When the
# vendor slows down, Lambda duration and concurrency rise, and so do the cache's
# connections, so this view points at the cache. Right: the per-dependency view,
# which points at the vendor, plus Valkey's own server-side latency in microseconds.
#
# Widgets use metric SEARCH expressions, so a new customer appears here without a
# change to this stack.
locals {
  cache_name = module.cache.name
  search = {
    duration    = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Duration\" catalog ${var.env}', 'p95', 60)"
    errors      = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Errors\" catalog ${var.env}', 'Sum', 60)"
    concurrency = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"ConcurrentExecutions\" ${var.env}', 'Maximum', 60)"
    dep_p95     = "SEARCH('{Showrunner,customer,dependency,env} MetricName=\"DependencyLatency\" env=\"${var.env}\"', 'p95', 60)"
    dep_errors  = "SEARCH('{Showrunner,customer,dependency,env} MetricName=\"DependencyErrors\" env=\"${var.env}\"', 'Sum', 60)"
  }

  half = 12
  widgets = [
    {
      type       = "text", x = 0, y = 0, width = local.half, height = 2,
      properties = { markdown = "## Legacy view\nService-level metrics: API latency, Lambda, and the cache's own connections/ECPUs. *Which dependency is slow? This view can't say.*" }
    },
    {
      type       = "text", x = local.half, y = 0, width = local.half, height = 2,
      properties = { markdown = "## Per-dependency view\nLatency and errors of **every dependency, per customer** (EMF), and Valkey's own server-side latency in **microseconds**." }
    },
    {
      type = "metric", x = 0, y = 2, width = local.half, height = 6,
      properties = {
        title   = "API latency p95 (catalog-api duration, ms)", region = var.region, view = "timeSeries", period = 60,
        metrics = [[{ expression = local.search.duration, id = "e1" }]]
      }
    },
    {
      type = "metric", x = local.half, y = 2, width = local.half, height = 6,
      properties = {
        title       = "Dependency latency p95 by customer (ms)", region = var.region, view = "timeSeries", period = 60,
        metrics     = [[{ expression = local.search.dep_p95, id = "e1" }]]
        annotations = { horizontal = [{ label = "vendor alarm", value = 1000 }] }
      }
    },
    {
      type = "metric", x = 0, y = 8, width = local.half, height = 6,
      properties = {
        title   = "Valkey: client connections and ECPUs", region = var.region, view = "timeSeries", period = 60,
        stacked = false,
        metrics = [
          ["AWS/ElastiCache", "CurrConnections", "clusterId", local.cache_name, { stat = "Maximum", label = "connections" }],
          ["AWS/ElastiCache", "ElastiCacheProcessingUnits", "clusterId", local.cache_name, { stat = "Sum", label = "ECPUs", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", x = local.half, y = 8, width = local.half, height = 6,
      properties = {
        title = "Valkey server-side latency (microseconds)", region = var.region, view = "timeSeries", period = 60,
        metrics = [
          ["AWS/ElastiCache", "SuccessfulReadRequestLatency", "clusterId", local.cache_name, { stat = "p99", label = "read p99 (µs)" }],
          ["AWS/ElastiCache", "SuccessfulWriteRequestLatency", "clusterId", local.cache_name, { stat = "p99", label = "write p99 (µs)" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 14, width = local.half, height = 6,
      properties = {
        title = "Lambda concurrency (account quota applies)", region = var.region, view = "timeSeries", period = 60,
        metrics = [
          [{ expression = local.search.concurrency, id = "e1" }],
          ["AWS/Lambda", "ConcurrentExecutions", { stat = "Maximum", label = "account total" }],
        ]
      }
    },
    {
      type = "metric", x = local.half, y = 14, width = local.half, height = 6,
      properties = {
        title   = "Dependency errors by customer (count/min)", region = var.region, view = "timeSeries", period = 60,
        metrics = [[{ expression = local.search.dep_errors, id = "e1" }]]
      }
    },
    {
      type = "metric", x = 0, y = 20, width = local.half, height = 6,
      properties = {
        title   = "Lambda errors", region = var.region, view = "timeSeries", period = 60,
        metrics = [[{ expression = local.search.errors, id = "e1" }]]
      }
    },
  ]
}

resource "aws_cloudwatch_dashboard" "env" {
  dashboard_name = "${local.name}-operations"
  dashboard_body = jsonencode({ widgets = local.widgets })
}
