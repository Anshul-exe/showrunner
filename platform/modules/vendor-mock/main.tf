# A pretend external recommendations vendor: a public Lambda function URL outside the
# VPC, so catalog-api calls it the way it would call a real vendor (out through the NAT
# instance, over public HTTPS). Chaos comes from each customer's AppConfig `chaos`
# profile (services/vendor-mock).

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "this" {
  # AppConfig Data ARNs use IDs, not names, and the apps belong to customer stacks
  # created later, so the scope is "any AppConfig app in this account": read-only.
  statement {
    sid       = "ReadChaosProfiles"
    actions   = ["appconfig:StartConfigurationSession", "appconfig:GetLatestConfiguration"]
    resources = ["arn:aws:appconfig:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:application/*"]
  }
}

module "function" {
  source = "../lambda-function"

  name                     = "sr-platform-${var.env}-vendor-mock"
  description              = "Mock external recommendations vendor (${var.env}); latency/errors from AppConfig chaos."
  handler                  = "vendor_mock.handler.handler"
  package_path             = var.package_path
  timeout                  = 10
  memory_size              = 512 # CPU scales with memory: 128 MB made cold starts ~6 s
  permissions_boundary_arn = var.permissions_boundary_arn
  policy_json              = data.aws_iam_policy_document.this.json
  environment = {
    ENV                    = var.env
    APPCONFIG_POLL_SECONDS = "15"
  }
}

resource "aws_lambda_function_url" "this" {
  #checkov:skip=CKV_AWS_258:Public on purpose: it stands in for a third-party vendor API
  function_name      = module.function.function_name
  qualifier          = module.function.alias_name
  authorization_type = "NONE" # it stands in for a public third-party API
}

# Since October 2025 a public function URL needs both permissions; the second one is
# limited to calls that arrive through the URL.
resource "aws_lambda_permission" "url" {
  #checkov:skip=CKV_AWS_301:Public on purpose: it stands in for a third-party vendor API
  statement_id           = "PublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = module.function.function_name
  qualifier              = module.function.alias_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "invoke_via_url" {
  #checkov:skip=CKV_AWS_301:Public on purpose, and only through the function URL (lambda:InvokedViaFunctionUrl)
  statement_id             = "PublicInvokeViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = module.function.function_name
  qualifier                = module.function.alias_name
  principal                = "*"
  invoked_via_function_url = true
}
