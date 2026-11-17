# GitHub's OIDC issuer. AWS validates GitHub's certificate chain itself, so no
# thumbprint is pinned.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
