# One Lambda function the platform way: an sr-* role with the workload permission
# boundary, a log group with retention and a data-protection policy, arm64, alias
# `live`. Used by catalog-api (per customer) and vendor-mock (per env).
# No reserved or provisioned concurrency: on-demand scaling keeps the Free plan cost at 0.
#
# Code ownership (ADR 0004, three change lanes): Terraform creates the function from
# the zip that tools/build/package.py makes, then ignores the code. Code changes ship
# through tools/deploy (lane 2: publish a version, move `live`), so an infra apply
# never rolls code back and a code deploy never needs Terraform.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  log_group_arn = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.name}"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = var.name
  description          = "Execution role for ${var.name}."
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  permissions_boundary = var.permissions_boundary_arn
}

# Only its own log group: no logs:CreateLogGroup on *, as AWSLambdaBasicExecutionRole
# would grant.
data "aws_iam_policy_document" "this" {
  source_policy_documents = var.policy_json == null ? [] : [var.policy_json]

  statement {
    sid       = "OwnLogGroup"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_group_arn}:*"]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${var.name}-inline"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.this.json
}

# ENI management for functions in a VPC (AWS managed, scoped by Lambda itself).
resource "aws_iam_role_policy_attachment" "vpc" {
  count      = var.vpc == null ? 0 : 1
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_cloudwatch_log_group" "this" {
  #checkov:skip=CKV_AWS_158:AWS-managed encryption; customer-managed KMS keys are on the never-create list
  #checkov:skip=CKV_AWS_338:Short retention on purpose (cost); this is a demo platform, not a system of record
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

# P8 backstop: if an email address reaches the logs despite the redacting logger,
# CloudWatch masks it for anyone without logs:Unmask, and the audit finding counts it.
resource "aws_cloudwatch_log_data_protection_policy" "this" {
  log_group_name = aws_cloudwatch_log_group.this.name
  policy_document = jsonencode({
    Name    = "${var.name}-data-protection"
    Version = "2021-06-01"
    Statement = [
      {
        Sid            = "Audit"
        DataIdentifier = ["arn:aws:dataprotection::aws:data-identifier/EmailAddress"]
        Operation      = { Audit = { FindingsDestination = {} } }
      },
      {
        Sid            = "Redact"
        DataIdentifier = ["arn:aws:dataprotection::aws:data-identifier/EmailAddress"]
        Operation      = { Deidentify = { MaskConfig = {} } }
      },
    ]
  })
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_116:No async invocations (API Gateway and function URL are synchronous), so no DLQ
  #checkov:skip=CKV_AWS_115:No reserved concurrency while the account quota is 10 (BRIEF section 3)
  #checkov:skip=CKV_AWS_173:Environment holds names and URLs only, no secrets; AWS-managed key
  #checkov:skip=CKV_AWS_272:Code signing needs Signer profiles; provenance is tier 2 (container image + attestation)
  #checkov:skip=CKV_AWS_50:X-Ray is tier 2 (per-dependency EMF metrics cover the core)
  #checkov:skip=CKV_AWS_117:vendor-mock is a public vendor stand-in and runs outside the VPC on purpose
  function_name    = var.name
  description      = var.description
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = var.handler
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  timeout          = var.timeout
  memory_size      = var.memory_size
  publish          = true

  environment {
    variables = var.environment
  }

  dynamic "vpc_config" {
    for_each = var.vpc == null ? [] : [var.vpc]
    content {
      subnet_ids         = vpc_config.value.subnet_ids
      security_group_ids = vpc_config.value.security_group_ids
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]

  lifecycle {
    # Lane 2 owns the code after creation (see the header).
    ignore_changes = [filename, source_code_hash]
  }
}

resource "aws_lambda_alias" "live" {
  name          = "live"
  description   = "What callers invoke. Moved by tools/deploy (lane 2)."
  function_name = aws_lambda_function.this.function_name
  # The latest published version. Lane 2 publishes and moves the alias; Terraform
  # reads the latest version on refresh, so both agree, and a config change made by
  # Terraform (publish = true) goes live in the same apply.
  function_version = aws_lambda_function.this.version
}
