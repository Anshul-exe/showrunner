terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
}

locals {
  claim = "token.actions.githubusercontent.com"
}

# Trust: only this repo (by immutable ID), only the named workflow file, and
# (when set) only jobs bound to a GitHub environment. A role that trusts
# "repo:owner/repo:*" would let any workflow on any branch assume it.
data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "GitHubActionsOIDC"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.claim}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.claim}:repository_id"
      values   = [tostring(var.repository_id)]
    }

    condition {
      test     = "StringLike"
      variable = "${local.claim}:sub"
      values   = [var.subject]
    }

    condition {
      test     = "StringLike"
      variable = "${local.claim}:job_workflow_ref"
      values   = var.job_workflow_refs
    }

    dynamic "condition" {
      for_each = var.environment == null ? [] : [var.environment]
      content {
        test     = "StringEquals"
        variable = "${local.claim}:environment"
        values   = [condition.value]
      }
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = var.name
  description          = var.description
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  permissions_boundary = var.permissions_boundary_arn
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "managed" {
  for_each   = toset(var.managed_policy_arns)
  role       = aws_iam_role.this.name
  policy_arn = each.value
}

resource "aws_iam_role_policy" "inline" {
  count  = var.inline_policy_json == null ? 0 : 1
  name   = "${var.name}-inline"
  role   = aws_iam_role.this.id
  policy = var.inline_policy_json
}
