# CI roles (BRIEF Pillar 0). Each one is assumable only by one workflow file, in one
# event context, from this repository. Names are referenced by the workflows.
#
#   role                       sub (event)                   workflow           what it can do
#   showrunner-ci-plan         pull_request                  plan.yml           read-only + state lockfile
#   showrunner-ci-apply-dev    environment:dev               apply.yml@main     build dev infra
#   showrunner-ci-apply-prod   environment:prod (reviewer)   apply.yml@main     build prod + ops-core infra
#   showrunner-ci-deploy-dev   environment:dev               build-deploy.yml   push image, publish version, start canary
#   showrunner-ci-deploy-prod  environment:prod              build-deploy.yml   same, prod
#   showrunner-ci-review-agents pull_request                 review-agents.yml  invoke one Bedrock model
#   showrunner-ci-approver     ref:refs/heads/main           approve.yml        SendTaskSuccess/Failure only

locals {
  # GitHub's immutable subject format (repos created after 15/07/2026):
  # repo:OWNER@OWNER_ID/NAME@REPO_ID:<context>. Names can be renamed or re-registered;
  # the IDs can't. Only `sub` changes; job_workflow_ref keeps owner/name. ADR 0009.
  sub_prefix = "repo:${split("/", local.repo)[0]}@${var.github_owner_id}/${split("/", local.repo)[1]}@${var.github_repository_id}"

  envs = {
    dev = {
      state_keys = ["shared/dev/*", "customers/*/dev/*"]
      functions  = ["${local.arn_prefix.lambda}:function:${local.workload_prefix}-*-dev-*"]
      cd_apps    = ["${local.arn_prefix.cd}:application:${local.workload_prefix}-*-dev-*", "${local.arn_prefix.cd}:deploymentgroup:${local.workload_prefix}-*-dev-*/*"]
      other_env  = "prod"
    }
    prod = {
      state_keys = ["shared/prod/*", "customers/*/prod/*", "ops-core/*"]
      functions = [
        "${local.arn_prefix.lambda}:function:${local.workload_prefix}-*-prod-*",
        "${local.arn_prefix.lambda}:function:${local.workload_prefix}-ops-*",
      ]
      cd_apps = [
        "${local.arn_prefix.cd}:application:${local.workload_prefix}-*-prod-*",
        "${local.arn_prefix.cd}:deploymentgroup:${local.workload_prefix}-*-prod-*/*",
        "${local.arn_prefix.cd}:application:${local.workload_prefix}-ops-*",
        "${local.arn_prefix.cd}:deploymentgroup:${local.workload_prefix}-ops-*/*",
      ]
      other_env = "dev"
    }
  }
}

# ---------------------------------------------------------------- plan (read-only)

data "aws_iam_policy_document" "plan" {
  # use_lockfile writes <key>.tflock next to the state, even for a plan.
  statement {
    sid       = "StateLockfile"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/*.tflock"]
  }

  # Plans run code from PR branches (providers, data sources), so the plan role must
  # not be able to read secrets even though ReadOnlyAccess covers most reads.
  statement {
    sid       = "DenySecrets"
    effect    = "Deny"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:GetParameterHistory"]
    resources = ["${local.arn_prefix.ssm}:parameter/showrunner/secret/*"]
  }

  statement {
    sid       = "DenyDecrypt"
    effect    = "Deny"
    actions   = ["kms:Decrypt", "secretsmanager:GetSecretValue"]
    resources = ["*"]
  }
}

module "role_plan" {
  source = "../modules/github-oidc-role"

  name                     = "${local.ci_prefix}-plan"
  description              = "terraform plan on pull requests. Read-only."
  oidc_provider_arn        = aws_iam_openid_connect_provider.github.arn
  repository_id            = var.github_repository_id
  subject                  = "${local.sub_prefix}:pull_request"
  job_workflow_refs        = ["${local.workflow_ref}/plan.yml@*"]
  permissions_boundary_arn = aws_iam_policy.ci_boundary.arn
  managed_policy_arns      = ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
  inline_policy_json       = data.aws_iam_policy_document.plan.json
}

# ---------------------------------------------------------------- apply-<env>

data "aws_iam_policy_document" "apply" {
  for_each = local.envs

  # PowerUserAccess has no IAM. Grant role/policy management only for sr-* names,
  # and only when the role carries the workload boundary.
  statement {
    sid = "ManageWorkloadRolesWithBoundary"
    actions = [
      "iam:CreateRole", "iam:PutRolePermissionsBoundary",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy",
    ]
    resources = ["${local.arn_prefix.iam}:role/${local.workload_prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.workload_boundary_arn]
    }
  }

  # Terraform refreshes every role, policy and instance profile it manages before it
  # plans. Read-only, and only for pipeline-owned names. (Missing in the first version:
  # found by the first CI apply, 18/11/2026.)
  statement {
    sid = "ReadWorkloadIam"
    actions = [
      "iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies", "iam:ListRoleTags", "iam:ListInstanceProfilesForRole",
      "iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions", "iam:ListPolicyTags",
      "iam:GetInstanceProfile", "iam:ListInstanceProfileTags",
    ]
    resources = [
      "${local.arn_prefix.iam}:role/${local.workload_prefix}-*",
      "${local.arn_prefix.iam}:policy/${local.workload_prefix}-*",
      "${local.arn_prefix.iam}:instance-profile/${local.workload_prefix}-*",
    ]
  }

  statement {
    sid = "ManageWorkloadRoles"
    actions = [
      "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
    ]
    resources = ["${local.arn_prefix.iam}:role/${local.workload_prefix}-*"]
  }

  # Roles may be handed only to the services the platform actually uses.
  statement {
    sid       = "PassWorkloadRolesToPlatformServices"
    actions   = ["iam:PassRole"]
    resources = ["${local.arn_prefix.iam}:role/${local.workload_prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "lambda.amazonaws.com", "states.amazonaws.com", "codedeploy.amazonaws.com",
        "ec2.amazonaws.com", "scheduler.amazonaws.com", "events.amazonaws.com",
        "apigateway.amazonaws.com", "appconfig.amazonaws.com", "vpc-flow-logs.amazonaws.com",
      ]
    }
  }

  statement {
    sid = "ManageWorkloadPolicies"
    actions = [
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion",
      "iam:TagPolicy", "iam:UntagPolicy",
    ]
    resources = ["${local.arn_prefix.iam}:policy/${local.workload_prefix}-*"]
  }

  statement {
    sid = "ManageWorkloadInstanceProfiles"
    actions = [
      "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile",
      "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile", "iam:UntagInstanceProfile",
    ]
    resources = ["${local.arn_prefix.iam}:instance-profile/${local.workload_prefix}-*"]
  }

  statement {
    sid = "StateReadWriteOwnKeys"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
    ]
    resources = [for k in each.value.state_keys : "${local.state_bucket_arn}/${k}"]
  }

  # PowerUserAccess grants s3:* on every bucket. Object writes are allowed only to
  # this environment's state keys and to pipeline-owned sr-* buckets, so a dev apply
  # can't overwrite prod state (or bootstrap state, or the trail).
  statement {
    sid     = "DenyObjectWritesOutsideScope"
    effect  = "Deny"
    actions = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
    not_resources = concat(
      [for k in each.value.state_keys : "${local.state_bucket_arn}/${k}"],
      ["arn:aws:s3:::${local.workload_prefix}-*/*"],
    )
  }

  # Best-effort isolation between environments for services that support resource
  # tags. Production would use separate accounts (ADR 0002).
  statement {
    sid       = "DenyOtherEnvResources"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/env"
      values   = [each.value.other_env]
    }
  }
}

module "role_apply" {
  source   = "../modules/github-oidc-role"
  for_each = local.envs

  name                     = "${local.ci_prefix}-apply-${each.key}"
  description              = "terraform apply for ${each.key} stacks, from apply.yml on main."
  oidc_provider_arn        = aws_iam_openid_connect_provider.github.arn
  repository_id            = var.github_repository_id
  subject                  = "${local.sub_prefix}:environment:${each.key}"
  environment              = each.key
  job_workflow_refs        = ["${local.workflow_ref}/apply.yml@refs/heads/main"]
  permissions_boundary_arn = aws_iam_policy.ci_boundary.arn
  managed_policy_arns      = ["arn:aws:iam::aws:policy/PowerUserAccess"]
  inline_policy_json       = data.aws_iam_policy_document.apply[each.key].json
}

# ---------------------------------------------------------------- deploy-<env>
# Lane 2 (app code): push an image, publish a Lambda version, start a CodeDeploy
# canary. No Terraform, no IAM, nothing else.

data "aws_iam_policy_document" "deploy" {
  for_each = local.envs

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
      "ecr:DescribeImages", "ecr:DescribeImageScanFindings",
    ]
    resources = ["${local.arn_prefix.ecr}:repository/${local.workload_prefix}/*"]
  }

  statement {
    sid = "LambdaPublish"
    actions = [
      "lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode",
      "lambda:PublishVersion", "lambda:GetAlias", "lambda:ListVersionsByFunction",
    ]
    resources = each.value.functions # the trailing * also matches :version and :alias
  }

  statement {
    sid = "CodeDeployCanary"
    actions = [
      "codedeploy:CreateDeployment", "codedeploy:GetDeployment", "codedeploy:GetDeploymentGroup",
      "codedeploy:GetApplication", "codedeploy:GetApplicationRevision",
      "codedeploy:RegisterApplicationRevision", "codedeploy:StopDeployment",
    ]
    resources = each.value.cd_apps
  }

  statement {
    sid       = "CodeDeployConfig"
    actions   = ["codedeploy:GetDeploymentConfig"]
    resources = ["${local.arn_prefix.cd}:deploymentconfig:*"]
  }

  # The deploy log feeds the incident loop's "deploys in the last 2 h" evidence.
  statement {
    sid       = "DeployLog"
    actions   = ["dynamodb:PutItem"]
    resources = ["${local.arn_prefix.ddb}:table/${local.workload_prefix}-ops-deploy-log"]
  }

  statement {
    sid     = "ReadStackOutputs"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "${local.arn_prefix.ssm}:parameter/showrunner/${each.key}/*",
      "${local.arn_prefix.ssm}:parameter/showrunner/bootstrap/*",
    ]
  }
}

module "role_deploy" {
  source   = "../modules/github-oidc-role"
  for_each = local.envs

  name                     = "${local.ci_prefix}-deploy-${each.key}"
  description              = "App-code deploys (image, Lambda version, canary) for ${each.key}."
  oidc_provider_arn        = aws_iam_openid_connect_provider.github.arn
  repository_id            = var.github_repository_id
  subject                  = "${local.sub_prefix}:environment:${each.key}"
  environment              = each.key
  job_workflow_refs        = ["${local.workflow_ref}/build-deploy.yml@refs/heads/main"]
  permissions_boundary_arn = aws_iam_policy.ci_boundary.arn
  inline_policy_json       = data.aws_iam_policy_document.deploy[each.key].json
}

# ---------------------------------------------------------------- review-agents

data "aws_iam_policy_document" "review_agents" {
  statement {
    sid       = "InvokeOneModel"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}"]
  }

  # Per-day cap on agent calls (a DynamoDB counter, created in phase 2/3).
  statement {
    sid       = "AgentCallCounter"
    actions   = ["dynamodb:UpdateItem", "dynamodb:GetItem"]
    resources = ["${local.arn_prefix.ddb}:table/${local.workload_prefix}-ops-agent-budget"]
  }
}

module "role_review_agents" {
  source = "../modules/github-oidc-role"

  name                     = "${local.ci_prefix}-review-agents"
  description              = "PR review agents: Bedrock invoke on one model, nothing else."
  oidc_provider_arn        = aws_iam_openid_connect_provider.github.arn
  repository_id            = var.github_repository_id
  subject                  = "${local.sub_prefix}:pull_request"
  job_workflow_refs        = ["${local.workflow_ref}/review-agents.yml@*"]
  permissions_boundary_arn = aws_iam_policy.ci_boundary.arn
  inline_policy_json       = data.aws_iam_policy_document.review_agents.json
}

# ---------------------------------------------------------------- approver
# SendTaskSuccess/Failure have no IAM resource type (Service Authorization
# Reference), so they can't be scoped to one state machine. The task token is the
# scope: it's unguessable and single-use. The trust pins approve.yml on main, and
# approve.yml checks that the commenter is a CODEOWNER. See ADR 0008.

data "aws_iam_policy_document" "approver" {
  statement {
    sid       = "AnswerTaskTokens"
    actions   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
    resources = ["*"]
  }
}

module "role_approver" {
  source = "../modules/github-oidc-role"

  name                     = "${local.ci_prefix}-approver"
  description              = "Turns a CODEOWNER's /approve comment into SendTaskSuccess."
  oidc_provider_arn        = aws_iam_openid_connect_provider.github.arn
  repository_id            = var.github_repository_id
  subject                  = "${local.sub_prefix}:ref:refs/heads/main"
  job_workflow_refs        = ["${local.workflow_ref}/approve.yml@refs/heads/main"]
  permissions_boundary_arn = aws_iam_policy.ci_boundary.arn
  inline_policy_json       = data.aws_iam_policy_document.approver.json
}
