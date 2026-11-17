# Partial configuration: the bucket name contains the account ID, which stays out of the
# repo. Pass it at init time:
#   terraform init -backend-config="bucket=showrunner-tfstate-<account-id>"
# The very first apply runs with local state, then migrates here (see README.md).
terraform {
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
