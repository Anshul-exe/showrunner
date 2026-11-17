# Cost guardrails: the first thing created in the account (BRIEF section 3).
#
# Credits are EXCLUDED on purpose. On the Free plan every charge is paid from
# credits, so a budget that nets credits off stays at $0 and never alerts, right up
# to the day the credits run out and the account closes.

locals {
  gross_cost_types = {
    include_credit             = false
    include_refund             = false
    include_discount           = true
    include_other_subscription = true
    include_recurring          = true
    include_subscription       = true
    include_support            = true
    include_tax                = true
    include_upfront            = true
    use_amortized              = false
    use_blended                = false
  }
}

resource "aws_budgets_budget" "monthly" {
  name         = "showrunner-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit             = local.gross_cost_types.include_credit
    include_refund             = local.gross_cost_types.include_refund
    include_discount           = local.gross_cost_types.include_discount
    include_other_subscription = local.gross_cost_types.include_other_subscription
    include_recurring          = local.gross_cost_types.include_recurring
    include_subscription       = local.gross_cost_types.include_subscription
    include_support            = local.gross_cost_types.include_support
    include_tax                = local.gross_cost_types.include_tax
    include_upfront            = local.gross_cost_types.include_upfront
    use_amortized              = local.gross_cost_types.use_amortized
    use_blended                = local.gross_cost_types.use_blended
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.alert_emails
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }
}

resource "aws_budgets_budget" "bedrock" {
  name         = "showrunner-bedrock"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.bedrock_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Bedrock"]
  }

  cost_types {
    include_credit             = local.gross_cost_types.include_credit
    include_refund             = local.gross_cost_types.include_refund
    include_discount           = local.gross_cost_types.include_discount
    include_other_subscription = local.gross_cost_types.include_other_subscription
    include_recurring          = local.gross_cost_types.include_recurring
    include_subscription       = local.gross_cost_types.include_subscription
    include_support            = local.gross_cost_types.include_support
    include_tax                = local.gross_cost_types.include_tax
    include_upfront            = local.gross_cost_types.include_upfront
    use_amortized              = local.gross_cost_types.use_amortized
    use_blended                = local.gross_cost_types.use_blended
  }

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.alert_emails
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }
}

# Proves the alert path end to end (phase 0 "done when": a budget email arrives).
# Fires on the first cent of gross spend; switch enable_smoke_budget off after.
resource "aws_budgets_budget" "smoke" {
  count = var.enable_smoke_budget ? 1 : 0

  name         = "showrunner-smoke-test"
  budget_type  = "COST"
  limit_amount = "1.00"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
    include_refund = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 0.01
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }
}

# Cost Anomaly Detection: ML on daily spend per service. Free. Needs Cost Explorer
# to have been opened once in the console, hence the flag.
resource "aws_ce_anomaly_monitor" "services" {
  count = var.enable_anomaly_detection ? 1 : 0

  name              = "showrunner-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "email" {
  count = var.enable_anomaly_detection ? 1 : 0

  name             = "showrunner-anomalies"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services[0].arn]

  dynamic "subscriber" {
    for_each = var.alert_emails
    content {
      type    = "EMAIL"
      address = subscriber.value
    }
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.anomaly_threshold_usd)]
    }
  }
}
