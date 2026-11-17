# 0006: One Valkey cache per environment, per-customer IAM-authenticated ACL users

Date: 18/11/2026. Status: accepted. **Changes BRIEF section 0**, which put per-customer
Valkey passwords in SSM SecureString in the core and IAM auth in tier 2.

## Context

- ElastiCache Serverless for Valkey bills a 100 MB data minimum (~$6.13/month). One
  cache per customer per environment multiplies that.
- Customers must not read each other's cached data.
- The brief planned passwords in SSM SecureString for the core. Two facts made that
  worse than the "harder" option:
  1. The read-only CI plan role (bootstrap) denies `kms:Decrypt` and the
     `/showrunner/secret/*` path, on purpose, because plans run PR code. Terraform
     refreshes an `aws_ssm_parameter` SecureString with decryption, so every customer
     stack's plan would fail on every PR.
  2. The password would sit in Terraform state anyway (`aws_elasticache_user` stores
     it), which the plan role can read.

## Decision

- **Pool, not silo:** one serverless cache per environment (`modules/cache`), shared
  by that environment's customers.
- **IAM authentication, no passwords anywhere:** each customer stack creates a Valkey
  user `sr-<customer>-<env>` with `authentication_mode = iam` and adds it to the env's
  user group. The function signs a 15-minute SigV4 token for `elasticache:Connect`
  (`services/catalog-api/catalog_api/cache.py`), minted fresh for each new connection.
  Each function's role may connect only as its own user.
- **ACLs:** the user's access string is `on ~<customer>:* +@all -@dangerous`: only its
  own key prefix, no FLUSHALL/KEYS/CONFIG.
- A VALKEY-engine user group needs no `default` user, so unauthenticated access is off.
  The group's only built-in member is `sr-<env>-ops` (`+ping` only).
- TLS is always on (serverless). Security group: 6379-6380 from the env's Lambda SG only.
- The connection is opened during Lambda init (TLS + token + cluster discovery takes
  ~1 s); measured per request, it made Valkey's client-side p95 look like ~1 s on cold
  containers, which would have muddied exactly the P9 "the cache isn't slow" story.

## Consequences

- No secret to rotate, leak or decrypt; plans work with the read-only role.
- IAM-authenticated connections drop after 12 hours; the client reconnects with a new
  token. `IamAuthenticationExpirations` and `AuthenticationFailures` are on the cache's
  metrics if that goes wrong.
- Noisy neighbours share ECPUs; serverless scales, and the cache has usage limits
  (1 GB, 1,000 ECPU/s) to cap cost.

## When to silo

A customer with strict isolation or data-residency needs, or one big enough to dominate
the shared cache: give them their own cache (the module already supports one per env).
In production, with an account per customer (ADR 0002), it's one per account anyway.
