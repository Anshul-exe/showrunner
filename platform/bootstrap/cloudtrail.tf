# One trail, management events only: the first copy of management events is free.
#
# Why a trail when Event history exists: EventBridge only receives "AWS API Call via
# CloudTrail" events when a trail is logging. Pillar 4b's alert on an unexpected
# AssumeRoleWithWebIdentity and Pillar 2b's change evidence depend on it.
#
# Single-region on purpose: the Free plan supports no multi-region features. IAM and
# STS are global and log to us-east-1, so include_global_service_events still covers
# them.

resource "aws_s3_bucket" "trail" {
  #checkov:skip=CKV_AWS_18:Access logs need a second bucket and add cost; CloudTrail records bucket-level API calls
  #checkov:skip=CKV_AWS_144:Cross-region replication isn't supported on the Free plan (no multi-region features)
  #checkov:skip=CKV_AWS_145:SSE-S3 on purpose: customer-managed KMS keys are on the never-create list ($1/month each)
  #checkov:skip=CKV2_AWS_62:No consumer for object events; nothing would read the notifications
  bucket = local.trail_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "trail" {
  bucket = aws_s3_bucket.trail.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket                  = aws_s3_bucket.trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "trail" {
  bucket = aws_s3_bucket.trail.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    id     = "expire-trail-logs"
    status = "Enabled"
    filter {}

    expiration {
      days = var.trail_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

locals {
  trail_arn = "arn:aws:cloudtrail:${var.region}:${var.account_id}:trail/${local.trail_name}"
}

data "aws_iam_policy_document" "trail_bucket" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.trail.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.trail.arn}/AWSLogs/${var.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.trail.arn, "${aws_s3_bucket.trail.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = data.aws_iam_policy_document.trail_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.trail]
}

resource "aws_cloudtrail" "this" {
  #checkov:skip=CKV2_AWS_10:CloudWatch Logs ingestion is $0.50/GB; EventBridge receives the events without it
  #checkov:skip=CKV_AWS_252:Per-log-file SNS notifications have no consumer
  #checkov:skip=CKV_AWS_35:SSE-S3 on purpose: customer-managed KMS keys are on the never-create list
  #checkov:skip=CKV_AWS_67:Free plan supports no multi-region features; global service events are included
  name                          = local.trail_name
  s3_bucket_name                = aws_s3_bucket.trail.id
  is_multi_region_trail         = false
  include_global_service_events = true
  enable_log_file_validation    = true

  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  depends_on = [aws_s3_bucket_policy.trail]
}
