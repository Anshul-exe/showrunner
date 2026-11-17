# Published to SSM for tools/sr and (day 2) the incident loop. Names and IDs only.
locals {
  published = {
    api_url                  = aws_apigatewayv2_api.this.api_endpoint
    function_name            = module.catalog_api.function_name
    table_name               = aws_dynamodb_table.catalog.name
    cache_user               = aws_elasticache_user.catalog.user_id
    appconfig_application_id = aws_appconfig_application.this.id
    appconfig_environment_id = aws_appconfig_environment.this.environment_id
    flags_profile_id         = aws_appconfig_configuration_profile.flags.configuration_profile_id
    chaos_profile_id         = aws_appconfig_configuration_profile.chaos.configuration_profile_id
    alarm_names = join(",", [
      aws_cloudwatch_metric_alarm.vendor_latency.alarm_name,
      aws_cloudwatch_metric_alarm.dependency_errors.alarm_name,
      aws_cloudwatch_metric_alarm.api_latency.alarm_name,
    ])
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:Non-secret names and IDs
  for_each = local.published

  name  = "/showrunner/${local.env}/customers/${local.customer}/${each.key}"
  type  = "String"
  tier  = "Standard"
  value = each.value
}

output "api_url" {
  description = "Base URL of the customer's API."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "function_name" {
  description = "catalog-api function name."
  value       = module.catalog_api.function_name
}

output "alarm_names" {
  description = "This customer's alarms."
  value       = local.published.alarm_names
}
