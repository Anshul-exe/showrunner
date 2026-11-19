# shared/<env>: what every customer in an environment shares.
#   network     VPC, subnets, NAT instance, gateway endpoints, flow logs
#   cache       ElastiCache Serverless Valkey + the user group customers join
#   vendor-mock the pretend external vendor (public, outside the VPC)
#   observability.tf  alerts topic, the env dashboard, the AppConfig strategy
# Customer stacks read this stack's outputs from SSM (outputs.tf), not remote state.

data "aws_ssm_parameter" "workload_boundary_arn" {
  name = "/showrunner/bootstrap/workload_boundary_arn"
}

locals {
  name     = "sr-${var.env}"
  boundary = data.aws_ssm_parameter.workload_boundary_arn.value
}

module "network" {
  source = "../modules/network"

  name                     = local.name
  region                   = var.region
  cidr                     = var.cidrs[var.env]
  azs                      = var.azs
  nat_ami_id               = var.nat_ami_id
  permissions_boundary_arn = local.boundary
}

module "cache" {
  source = "../modules/cache"

  name                      = local.name
  vpc_id                    = module.network.vpc_id
  subnet_ids                = module.network.private_subnet_ids
  client_security_group_ids = [module.network.lambda_security_group_id]
}

module "vendor_mock" {
  source = "../modules/vendor-mock"

  env                      = var.env
  package_path             = "${path.root}/${var.packages_dir}/vendor-mock.zip"
  permissions_boundary_arn = local.boundary
}
