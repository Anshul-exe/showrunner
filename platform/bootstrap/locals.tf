locals {
  state_bucket = "showrunner-tfstate-${var.account_id}"
  trail_bucket = "showrunner-trail-${var.account_id}"
  trail_name   = "showrunner-trail"

  # Built from names, not resource attributes, so policies are known at plan time.
  state_bucket_arn = "arn:aws:s3:::${local.state_bucket}"

  # Naming contract for later stacks (the boundaries and roles depend on it):
  #   showrunner-ci-*   CI roles and their boundary: created here, nowhere else
  #   sr-*              everything the pipeline creates (roles, functions, tables, repos)
  ci_prefix       = "showrunner-ci"
  workload_prefix = "sr"

  repo         = var.github_repository
  workflow_ref = "${var.github_repository}/.github/workflows"

  arn_prefix = {
    iam    = "arn:aws:iam::${var.account_id}"
    lambda = "arn:aws:lambda:${var.region}:${var.account_id}"
    ddb    = "arn:aws:dynamodb:${var.region}:${var.account_id}"
    ssm    = "arn:aws:ssm:${var.region}:${var.account_id}"
    ecr    = "arn:aws:ecr:${var.region}:${var.account_id}"
    cd     = "arn:aws:codedeploy:${var.region}:${var.account_id}"
  }
}
