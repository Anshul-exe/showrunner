# Other stacks read bootstrap's outputs from SSM Parameter Store (standard tier: free),
# not from remote state, so a stack's role never needs read access to another
# stack's state file (BRIEF section 5).

locals {
  published = {
    state_bucket          = aws_s3_bucket.state.id
    oidc_provider_arn     = aws_iam_openid_connect_provider.github.arn
    workload_boundary_arn = aws_iam_policy.workload_boundary.arn
    role_plan_arn         = module.role_plan.arn
    role_apply_dev_arn    = module.role_apply["dev"].arn
    role_apply_prod_arn   = module.role_apply["prod"].arn
    role_deploy_dev_arn   = module.role_deploy["dev"].arn
    role_deploy_prod_arn  = module.role_deploy["prod"].arn
    role_review_arn       = module.role_review_agents.arn
    role_approver_arn     = module.role_approver.arn
    trail_name            = aws_cloudtrail.this.name
    bedrock_model_id      = var.bedrock_model_id
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:Non-secret names and ARNs; SecureString would make every reader need kms:Decrypt
  for_each = local.published

  name  = "/showrunner/bootstrap/${each.key}"
  type  = "String"
  tier  = "Standard"
  value = each.value
}

output "state_bucket" {
  description = "Backend bucket for every stack."
  value       = aws_s3_bucket.state.id
}

output "role_arns" {
  description = "CI role ARNs."
  value = {
    plan          = module.role_plan.arn
    apply_dev     = module.role_apply["dev"].arn
    apply_prod    = module.role_apply["prod"].arn
    deploy_dev    = module.role_deploy["dev"].arn
    deploy_prod   = module.role_deploy["prod"].arn
    review_agents = module.role_review_agents.arn
    approver      = module.role_approver.arn
  }
}

output "workload_boundary_arn" {
  description = "Every role a pipeline creates must carry this boundary."
  value       = aws_iam_policy.workload_boundary.arn
}
