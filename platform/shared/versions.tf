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

  default_tags {
    tags = {
      customer    = "platform"
      env         = var.env
      owner       = "platform-team"
      cost-center = "showrunner"
      managed-by  = "terraform"
      stack       = "shared/${var.env}"
    }
  }
}
