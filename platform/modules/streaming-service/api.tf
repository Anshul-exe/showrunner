# HTTP API in front of catalog-api's `live` alias. Throttling here protects the
# account's Lambda concurrency (10 until the quota increase lands): one customer's
# burst can't starve the other.

resource "aws_cloudwatch_log_group" "api" {
  #checkov:skip=CKV_AWS_158:AWS-managed encryption; customer-managed KMS keys are on the never-create list
  #checkov:skip=CKV_AWS_338:Short retention on purpose (cost)
  name              = "/sr/${local.name}/api-access"
  retention_in_days = 7
}

resource "aws_apigatewayv2_api" "this" {
  name          = local.name
  description   = "${local.customer} ${local.env} catalog API"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "catalog_api" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.catalog_api.alias_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 8000
}

resource "aws_apigatewayv2_route" "default" {
  #checkov:skip=CKV_AWS_309:A public read-only catalog for viewers; no authorizer by design
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.catalog_api.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  # No source IP or user agent: viewers' personal data stays out of the logs (P8).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      routeKey           = "$context.routeKey"
      path               = "$context.path"
      status             = "$context.status"
      responseLatency    = "$context.responseLatency"
      integrationLatency = "$context.integrationLatency"
      integrationError   = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = module.catalog_api.function_name
  qualifier     = module.catalog_api.alias_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*"
}
