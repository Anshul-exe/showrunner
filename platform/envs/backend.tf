# Partial configuration. tools/tf.sh (and CI) pass:
#   -backend-config="bucket=showrunner-tfstate-<account-id>"
#   -backend-config="key=customers/<name>/<env>/terraform.tfstate"
terraform {
  backend "s3" {
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
