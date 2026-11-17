# Account-wide safe defaults. Both are free.

# No bucket in this account can be made public. CloudFront reaches the site buckets
# through Origin Access Control, which works with this on.
resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The NAT instances' root volumes (phase 1) get encrypted with the AWS-managed EBS key.
resource "aws_ebs_encryption_by_default" "this" {
  enabled = true
}
