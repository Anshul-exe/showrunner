# Partial configuration. tools/tf.sh (and CI) pass:
#   -backend-config="bucket=showrunner-tfstate-<account-id>"
#   -backend-config="key=ops-core/terraform.tfstate"
# The ops plane's state belongs to the prod apply role (bootstrap roles.tf): it can read
# the GitHub App key and change customers' runtime config, so CI applies it only behind
# the `prod` environment's required reviewer.
terraform {
  backend "s3" {
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
