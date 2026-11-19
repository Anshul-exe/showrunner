# Published to SSM Parameter Store (standard tier, free) for customer stacks and
# tools/sr. Names and IDs only; nothing secret.

locals {
  published = {
    vpc_id                   = module.network.vpc_id
    private_subnet_ids       = join(",", module.network.private_subnet_ids)
    lambda_security_group_id = module.network.lambda_security_group_id
    private_route_table_id   = module.network.private_route_table_id
    nat_asg_name             = module.network.nat_asg_name
    flow_log_group           = module.network.flow_log_group
    resolver_log_group       = module.network.resolver_log_group
    cache_name               = module.cache.name
    cache_arn                = module.cache.arn
    cache_endpoint           = module.cache.endpoint
    cache_port               = tostring(module.cache.port)
    cache_user_group_id      = module.cache.user_group_id
    vendor_url               = module.vendor_mock.url
    vendor_function_name     = module.vendor_mock.function_name
    alerts_topic_arn         = aws_sns_topic.alerts.arn
    appconfig_strategy_id    = aws_appconfig_deployment_strategy.runtime.id
    dashboard_name           = aws_cloudwatch_dashboard.env.dashboard_name
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:Non-secret names and IDs; SecureString would make every reader need kms:Decrypt
  for_each = local.published

  name  = "/showrunner/${var.env}/shared/${each.key}"
  type  = "String"
  tier  = "Standard"
  value = each.value
}

output "vendor_url" {
  description = "Mock vendor URL."
  value       = module.vendor_mock.url
}

output "cache_endpoint" {
  description = "Valkey endpoint."
  value       = module.cache.endpoint
}

output "dashboard_url" {
  description = "The env dashboard."
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.env.dashboard_name}"
}
