# ops-core: the account's operations plane (one per account, serves var.envs).
# Today: the incident loop. Tier 3 adds the scheduler jobs, cost report and MCP server.

data "aws_ssm_parameter" "workload_boundary_arn" {
  name = "/showrunner/bootstrap/workload_boundary_arn"
}

module "ops_core" {
  source = "../modules/ops-core"

  envs                     = var.envs
  github_repository        = var.github_repository
  runbook_base_url         = var.runbook_base_url
  package_path             = "${path.root}/${var.packages_dir}/incident.zip"
  permissions_boundary_arn = data.aws_ssm_parameter.workload_boundary_arn.value
}

output "state_machine_arn" {
  value = module.ops_core.state_machine_arn
}

output "function_names" {
  value = module.ops_core.function_names
}
