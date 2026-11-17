terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
}

provider "aws" {
  region = var.region

  # Hard stop if the credentials belong to any other account (CLAUDE.md rule 1).
  allowed_account_ids = [var.account_id]

  # The five cost tags (Pillar 6) come from the customer's YAML.
  default_tags {
    tags = {
      customer    = local.spec.customer
      env         = local.spec.env
      owner       = local.spec.owner
      cost-center = local.spec.cost_center
      managed-by  = "terraform"
      stack       = "customers/${local.spec.customer}/${local.spec.env}"
    }
  }
}
