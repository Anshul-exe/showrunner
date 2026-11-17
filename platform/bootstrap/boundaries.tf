# Two permission boundaries. A boundary is a ceiling: the role's effective permissions
# are the intersection of its own policies and the boundary. Each boundary allows
# everything, then denies what no pipeline role may ever do.
#
#   showrunner-ci-boundary    on the CI roles created here
#   sr-workload-boundary      on every role the pipeline creates (Lambda, Step
#                             Functions, NAT instance...). CI may only create roles
#                             that carry it, so a pipeline can't mint a role that
#                             escapes the guardrails.

locals {
  workload_boundary_arn = "${local.arn_prefix.iam}:policy/${local.workload_prefix}-workload-boundary"

  # Calls to global services report us-east-1, but list them so the region lock never
  # breaks them.
  global_actions = [
    "iam:*", "sts:*", "cloudfront:*", "route53:*", "budgets:*", "ce:*",
    "support:*", "health:*", "pricing:*", "account:*", "freetier:*",
    "billing:*", "tag:*", "wafv2:*", "acm:*", "s3:ListAllMyBuckets",
  ]
}

data "aws_iam_policy_document" "common_denies" {
  # A permission boundary is a ceiling, not a grant: "Allow *" minus explicit denies is
  # the intended shape. The roles' own policies are what grant access.
  #checkov:skip=CKV_AWS_1:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_49:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_107:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_108:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_109:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_110:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_111:Boundary ceiling, not a grant
  #checkov:skip=CKV_AWS_356:Boundary ceiling, not a grant
  statement {
    sid       = "AllowWithinBoundary"
    actions   = ["*"]
    resources = ["*"]
  }

  statement {
    sid         = "DenyOutsideHomeRegion"
    effect      = "Deny"
    not_actions = local.global_actions
    resources   = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  # The never-create list (BRIEF section 3). conftest checks the plan too; this is
  # the backstop if a policy check is skipped or a console/CLI call is made with a
  # pipeline role.
  statement {
    sid    = "DenyNeverCreate"
    effect = "Deny"
    actions = [
      "eks:CreateCluster", "eks:CreateNodegroup", "eks:CreateFargateProfile",
      "ec2:CreateNatGateway",
      "rds:CreateDBInstance", "rds:CreateDBCluster", "rds:RestoreDBInstanceFromDBSnapshot",
      "rds:RestoreDBClusterFromSnapshot", "rds:CreateDBInstanceReadReplica",
      "mq:CreateBroker",
      "es:CreateDomain", "es:CreateElasticsearchDomain", "aoss:CreateCollection",
      "elasticloadbalancing:CreateLoadBalancer",
      "kms:CreateKey",
      "secretsmanager:CreateSecret",
      "ec2:CreateTransitGateway",
      "ec2:PurchaseReservedInstancesOffering", "ec2:CreateCapacityReservation",
      "savingsplans:CreateSavingsPlan",
      "route53domains:RegisterDomain",
      "organizations:*", "controltower:*",
      "iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile",
    ]
    resources = ["*"]
  }

  # The only EC2 instances are the NAT instances.
  statement {
    sid       = "DenyInstanceTypesOtherThanNat"
    effect    = "Deny"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:*:*:instance/*"]
    condition {
      test     = "StringNotEquals"
      variable = "ec2:InstanceType"
      values   = ["t4g.micro"]
    }
  }

  # Nobody in the pipeline can blind the guardrails.
  statement {
    sid    = "DenyGuardrailTampering"
    effect = "Deny"
    actions = [
      "cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail",
      "cloudtrail:PutEventSelectors",
    ]
    resources = [local.trail_arn]
  }

  statement {
    sid       = "DenyAccountBudgetChanges"
    effect    = "Deny"
    actions   = ["budgets:ModifyBudget", "budgets:DeleteBudgetAction", "budgets:UpdateBudgetAction"]
    resources = ["arn:aws:budgets::${var.account_id}:budget/showrunner-*"]
  }

  statement {
    sid    = "DenyAnomalyDetectionChanges"
    effect = "Deny"
    actions = [
      "ce:DeleteAnomalyMonitor", "ce:UpdateAnomalyMonitor",
      "ce:DeleteAnomalySubscription", "ce:UpdateAnomalySubscription",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DenyBootstrapBucketChanges"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
      "s3:PutBucketVersioning", "s3:PutLifecycleConfiguration",
      "s3:PutEncryptionConfiguration", "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketOwnershipControls",
    ]
    resources = [
      "arn:aws:s3:::${local.state_bucket}",
      "arn:aws:s3:::${local.trail_bucket}",
    ]
  }

  statement {
    sid       = "DenyTrailLogTampering"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.trail_bucket}/*"]
  }

  statement {
    sid    = "DenyAccountSafetyChanges"
    effect = "Deny"
    actions = [
      "s3:PutAccountPublicAccessBlock", "ec2:DisableEbsEncryptionByDefault",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DenyCiIdentityChanges"
    effect = "Deny"
    not_actions = [
      "iam:Get*", "iam:List*", "iam:Simulate*", "iam:Generate*",
    ]
    resources = [
      "${local.arn_prefix.iam}:role/${local.ci_prefix}-*",
      "${local.arn_prefix.iam}:policy/${local.ci_prefix}-*",
      local.workload_boundary_arn,
      "${local.arn_prefix.iam}:oidc-provider/*",
    ]
  }

  statement {
    sid       = "DenyBoundaryRemoval"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }

  statement {
    sid       = "DenyBootstrapParameterChanges"
    effect    = "Deny"
    actions   = ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters", "ssm:LabelParameterVersion"]
    resources = ["${local.arn_prefix.ssm}:parameter/showrunner/bootstrap/*"]
  }
}

# CI roles may create roles only if the new role carries the workload boundary.
data "aws_iam_policy_document" "ci_boundary" {
  source_policy_documents = [data.aws_iam_policy_document.common_denies.json]

  statement {
    sid       = "DenyRoleWithoutWorkloadBoundary"
    effect    = "Deny"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.workload_boundary_arn]
    }
  }
}

# Workload roles (Lambdas etc.) can read IAM (the incident loop simulates policies)
# but never write it.
data "aws_iam_policy_document" "workload_boundary" {
  source_policy_documents = [data.aws_iam_policy_document.common_denies.json]

  statement {
    sid         = "DenyIamWrites"
    effect      = "Deny"
    not_actions = ["iam:Get*", "iam:List*", "iam:Simulate*", "iam:Generate*"]
    resources   = ["arn:aws:iam::*:*"]
  }
}

resource "aws_iam_policy" "ci_boundary" {
  name        = "${local.ci_prefix}-boundary"
  description = "Ceiling for Showrunner CI roles (GitHub OIDC)."
  policy      = data.aws_iam_policy_document.ci_boundary.json
}

resource "aws_iam_policy" "workload_boundary" {
  name        = "${local.workload_prefix}-workload-boundary"
  description = "Ceiling for every role the Showrunner pipeline creates."
  policy      = data.aws_iam_policy_document.workload_boundary.json
}
