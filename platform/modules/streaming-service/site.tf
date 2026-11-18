# The customer page (tier 2, features.customer_page): what a viewer of this customer
# sees. One CloudFront distribution:
#
#   /*      -> S3 bucket (private; only this distribution may read it, via OAC)
#   /api/*  -> the customer's HTTP API, "/api" stripped by a CloudFront Function,
#              never cached (every call is live, so the page shows real latency)
#
# Pay-as-you-go CloudFront: always free up to 1 TB out, 10M requests and 2M function
# invocations a month (docs/COST.md). No Lambda@Edge (not on the Free plan).

locals {
  site_enabled = var.spec.features.customer_page
  site_files = {
    "index.html" = "text/html; charset=utf-8"
    "app.js"     = "text/javascript; charset=utf-8"
    "style.css"  = "text/css; charset=utf-8"
  }
  site_config = jsonencode({
    customer     = local.customer
    env          = local.env
    catalog      = var.spec.catalog
    display_name = title(replace(local.customer, "-", " "))
  })
}

data "aws_caller_identity" "site" {
  count = local.site_enabled ? 1 : 0
}

data "aws_cloudfront_cache_policy" "optimized" {
  count = local.site_enabled ? 1 : 0
  name  = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  count = local.site_enabled ? 1 : 0
  name  = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_but_host" {
  count = local.site_enabled ? 1 : 0
  name  = "Managed-AllViewerExceptHostHeader"
}

# ------------------------------------------------------------------ bucket
resource "aws_s3_bucket" "site" {
  #checkov:skip=CKV_AWS_18:Access logs cost more than the page; CloudFront is the only reader
  #checkov:skip=CKV_AWS_144:Static page rebuilt from the repo; replication adds nothing
  #checkov:skip=CKV2_AWS_62:No event consumers
  #checkov:skip=CKV_AWS_145:SSE-S3; customer-managed KMS keys are on the never-create list
  count         = local.site_enabled ? 1 : 0
  bucket        = "${local.name}-site-${data.aws_caller_identity.site[0].account_id}"
  force_destroy = true # contents are rebuilt from the repo
}

resource "aws_s3_bucket_public_access_block" "site" {
  count                   = local.site_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.site[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  count  = local.site_enabled ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  count  = local.site_enabled ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "site" {
  count  = local.site_enabled ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  versioning_configuration {
    status = "Enabled" # a bad page upload is one version back
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "site" {
  count  = local.site_enabled ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  rule {
    id     = "old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
  depends_on = [aws_s3_bucket_versioning.site]
}

data "aws_iam_policy_document" "site" {
  count = local.site_enabled ? 1 : 0

  statement {
    sid       = "CloudFrontOnly"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site[0].arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site[0].arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.site[0].arn, "${aws_s3_bucket.site[0].arn}/*"]
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

resource "aws_s3_bucket_policy" "site" {
  count      = local.site_enabled ? 1 : 0
  bucket     = aws_s3_bucket.site[0].id
  policy     = data.aws_iam_policy_document.site[0].json
  depends_on = [aws_s3_bucket_public_access_block.site]
}

# Page files: short browser/edge cache so a change shows within 5 minutes without an
# invalidation (CachingOptimized honours the object's Cache-Control).
resource "aws_s3_object" "site" {
  for_each      = local.site_enabled ? local.site_files : {}
  bucket        = aws_s3_bucket.site[0].id
  key           = each.key
  source        = "${path.module}/site/${each.key}"
  etag          = filemd5("${path.module}/site/${each.key}")
  content_type  = each.value
  cache_control = "public, max-age=300"
}

resource "aws_s3_object" "site_config" {
  count         = local.site_enabled ? 1 : 0
  bucket        = aws_s3_bucket.site[0].id
  key           = "config.json"
  content       = local.site_config
  content_type  = "application/json"
  cache_control = "public, max-age=300"
}

# ------------------------------------------------------------------ CloudFront
resource "aws_cloudfront_origin_access_control" "site" {
  count                             = local.site_enabled ? 1 : 0
  name                              = "${local.name}-site"
  description                       = "${local.customer} ${local.env} page bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "strip_api" {
  count   = local.site_enabled ? 1 : 0
  name    = "${local.name}-strip-api"
  runtime = "cloudfront-js-2.0"
  comment = "/api/catalog -> /catalog for the HTTP API origin"
  publish = true
  code    = file("${path.module}/site/strip-api.js")
}

resource "aws_cloudfront_response_headers_policy" "site" {
  count   = local.site_enabled ? 1 : 0
  name    = "${local.name}-site"
  comment = "Strict CSP: the page loads only its own files and calls only /api"

  security_headers_config {
    content_security_policy {
      content_security_policy = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
      override                = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  #checkov:skip=CKV_AWS_68:WAF costs ~$5+/month per web ACL; the API behind it is throttled (api.tf)
  #checkov:skip=CKV2_AWS_47:No WAF (see CKV_AWS_68)
  #checkov:skip=CKV_AWS_86:Standard logs cost more than the page; API access logs exist (api.tf)
  #checkov:skip=CKV_AWS_310:Origin failover is tier 3 (P12); one region by design
  #checkov:skip=CKV_AWS_374:A streaming customer chooses its markets; demo has none
  #checkov:skip=CKV_AWS_174:Default *.cloudfront.net certificate until the custom domain (tier 3)
  #checkov:skip=CKV2_AWS_42:Default *.cloudfront.net certificate until the custom domain (tier 3)
  count               = local.site_enabled ? 1 : 0
  enabled             = true
  comment             = "${local.customer} ${local.env} customer page"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"
  is_ipv6_enabled     = true

  origin {
    origin_id                = "page"
    domain_name              = aws_s3_bucket.site[0].bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site[0].id
  }

  origin {
    origin_id   = "api"
    domain_name = replace(aws_apigatewayv2_api.this.api_endpoint, "https://", "")
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 10
    }
  }

  default_cache_behavior {
    target_origin_id           = "page"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized[0].id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site[0].id
  }

  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled[0].id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_but_host[0].id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site[0].id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.strip_api[0].arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "${local.name}-site" }
}
