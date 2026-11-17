# The golden path (BRIEF Pillar 1): one customer environment from one YAML file.
#
#   HTTP API -> catalog-api Lambda (alias live, in the env's private subnets)
#                 -> Valkey (this customer's ACL user, keys "<customer>:*", IAM auth)
#                 -> DynamoDB on a miss (this customer's table)
#                 -> the vendor for recommendations (via the NAT instance)
#   AppConfig app with `flags` and `chaos` profiles (lane 1: runtime changes)
#   alarms with runbook links -> the env's on-call topic
#
# Everything env-wide (VPC, cache, vendor, alerts topic, dashboard) comes from the
# shared/<env> stack through SSM parameters.

data "aws_ssm_parameters_by_path" "shared" {
  path = "/showrunner/${var.spec.env}/shared"
}

data "aws_ssm_parameter" "workload_boundary_arn" {
  name = "/showrunner/bootstrap/workload_boundary_arn"
}

locals {
  customer = var.spec.customer
  env      = var.spec.env
  name     = "sr-${local.customer}-${local.env}"
  boundary = data.aws_ssm_parameter.workload_boundary_arn.value

  # Names and IDs only (see shared/outputs.tf); the provider marks every value from
  # this data source sensitive by default.
  shared = nonsensitive(zipmap(
    [for n in data.aws_ssm_parameters_by_path.shared.names : basename(n)],
    data.aws_ssm_parameters_by_path.shared.values,
  ))

  seed = jsondecode(file("${path.module}/seed/${var.spec.catalog}.json"))
}

# ------------------------------------------------------------------ DynamoDB
resource "aws_dynamodb_table" "catalog" {
  #checkov:skip=CKV_AWS_119:AWS-owned key; customer-managed KMS keys are on the never-create list
  name                        = "${local.name}-catalog"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = local.env == "prod"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = local.env == "prod" || var.spec.features.pitr
  }
}

resource "aws_dynamodb_table_item" "title" {
  for_each = { for t in local.seed.titles : t.id => t }

  table_name = aws_dynamodb_table.catalog.name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk    = { S = "title" }
    sk    = { S = each.key }
    name  = { S = each.value.name }
    genre = { S = each.value.genre }
    year  = { N = tostring(each.value.year) }
  })
}

# The fallback copy of recommendations. The app refreshes these from the vendor, so
# Terraform only seeds them.
resource "aws_dynamodb_table_item" "recs" {
  for_each = local.seed.recs

  table_name = aws_dynamodb_table.catalog.name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk   = { S = "recs" }
    sk   = { S = each.key }
    recs = { L = [for r in each.value : { S = r }] }
  })

  lifecycle {
    ignore_changes = [item]
  }
}

# ------------------------------------------------------------------ Valkey user
# This customer's identity in the shared cache: IAM-authenticated, and the ACL only
# allows keys under "<customer>:". Another customer's function can't read these keys
# even with a bug, and can't sign a token for this user (elasticache:Connect is
# scoped to the user ARN in each role).
resource "aws_elasticache_user" "catalog" {
  user_id       = local.name
  user_name     = local.name
  engine        = "valkey"
  access_string = "on ~${local.customer}:* +@all -@dangerous"

  authentication_mode {
    type = "iam"
  }
}

resource "aws_elasticache_user_group_association" "catalog" {
  user_group_id = local.shared.cache_user_group_id
  user_id       = aws_elasticache_user.catalog.user_id
}

# ------------------------------------------------------------------ catalog-api
data "aws_iam_policy_document" "catalog_api" {
  statement {
    sid       = "ReadCatalog"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.catalog.arn]
  }

  # Writes only to the "recs" partition (the fallback copy).
  statement {
    sid       = "RefreshFallbackRecs"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.catalog.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["recs"]
    }
  }

  statement {
    sid     = "ConnectAsOwnCacheUser"
    actions = ["elasticache:Connect"]
    resources = [
      local.shared.cache_arn,
      aws_elasticache_user.catalog.arn,
    ]
  }

  statement {
    sid       = "ReadOwnRuntimeConfig"
    actions   = ["appconfig:StartConfigurationSession", "appconfig:GetLatestConfiguration"]
    resources = ["${aws_appconfig_application.this.arn}/*"]
  }
}

module "catalog_api" {
  source = "../lambda-function"

  name                     = "${local.name}-catalog-api"
  description              = "${local.customer} ${local.env}: catalog and recommendations API."
  handler                  = "catalog_api.handler.handler"
  package_path             = var.catalog_api_package
  timeout                  = 6   # > vendor timeout (2.5 s) + cache/DynamoDB timeouts
  memory_size              = 512 # CPU scales with memory; keeps cold starts well under the vendor timeout
  permissions_boundary_arn = local.boundary
  policy_json              = data.aws_iam_policy_document.catalog_api.json

  vpc = {
    subnet_ids         = split(",", local.shared.private_subnet_ids)
    security_group_ids = [local.shared.lambda_security_group_id]
  }

  environment = {
    CUSTOMER               = local.customer
    ENV                    = local.env
    TABLE_NAME             = aws_dynamodb_table.catalog.name
    CACHE_ENDPOINT         = local.shared.cache_endpoint
    CACHE_PORT             = local.shared.cache_port
    CACHE_NAME             = local.shared.cache_name
    CACHE_USER             = aws_elasticache_user.catalog.user_id
    VENDOR_URL             = local.shared.vendor_url
    VENDOR_TIMEOUT_S       = "2.5"
    APPCONFIG_APPLICATION  = aws_appconfig_application.this.name
    APPCONFIG_ENVIRONMENT  = aws_appconfig_environment.this.name
    APPCONFIG_POLL_SECONDS = "15"
  }

  depends_on = [aws_elasticache_user_group_association.catalog]
}
