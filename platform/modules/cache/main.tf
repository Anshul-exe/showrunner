# ElastiCache Serverless for Valkey, one per environment, shared by its customers
# (ADR 0006: pool, not silo). Isolation between customers is by Valkey ACL users: each
# customer stack adds its own IAM-authenticated user, limited to keys "<customer>:*",
# to the user group created here.
#
#   - TLS is always on for serverless caches
#   - a VALKEY user group has no default user: unauthenticated access is off
#   - no passwords anywhere: users authenticate with IAM (elasticache:Connect)
#   - usage limits cap the bill (Valkey's minimum is 100 MB of data storage)

resource "aws_security_group" "cache" {
  name        = "${var.name}-cache"
  description = "Valkey: TLS from the env Lambda security group only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Valkey (6379 primary, 6380 reader) from catalog-api"
    from_port       = 6379
    to_port         = 6380
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  tags = { Name = "${var.name}-cache" }
}

# A health-check-only user so the group is never empty. Customers' users are added
# by their own stacks (aws_elasticache_user_group_association).
resource "aws_elasticache_user" "ops" {
  user_id       = "${var.name}-ops"
  user_name     = "${var.name}-ops"
  engine        = "valkey"
  access_string = "on -@all +ping"

  authentication_mode {
    type = "iam"
  }
}

resource "aws_elasticache_user_group" "this" {
  user_group_id = "${var.name}-users"
  engine        = "valkey"
  user_ids      = [aws_elasticache_user.ops.user_id]

  lifecycle {
    # Customer stacks add their users with associations; don't remove them.
    ignore_changes = [user_ids]
  }
}

resource "aws_elasticache_serverless_cache" "this" {
  name                 = "${var.name}-cache"
  description          = "Valkey for ${var.name}: shared, per-customer ACL users"
  engine               = "valkey"
  major_engine_version = var.major_engine_version
  subnet_ids           = var.subnet_ids
  security_group_ids   = [aws_security_group.cache.id]
  user_group_id        = aws_elasticache_user_group.this.user_group_id

  cache_usage_limits {
    data_storage {
      maximum = 1
      unit    = "GB"
    }
    ecpu_per_second {
      maximum = 1000
    }
  }

  snapshot_retention_limit = 0 # a cache: the source of truth is DynamoDB
}
