# Thin root: one customer environment = one YAML file = one state
# (key customers/<name>/<env>). All logic is in modules/streaming-service.

locals {
  spec = yamldecode(file(var.spec_file))
}

module "service" {
  source = "../modules/streaming-service"

  spec                = local.spec
  catalog_api_package = "${path.root}/${var.packages_dir}/catalog-api.zip"
  runbook_base_url    = var.runbook_base_url
}

output "api_url" {
  description = "Customer API base URL."
  value       = module.service.api_url
}

output "function_name" {
  description = "catalog-api function."
  value       = module.service.function_name
}

output "site_url" {
  description = "Customer page, if features.customer_page is on."
  value       = module.service.site_url
}
