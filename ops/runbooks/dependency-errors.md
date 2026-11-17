---
alarm: dependency-errors
severity: sev2
owner: platform-team
metric: sum of Showrunner/DependencyErrors {customer, env, dependency in valkey, dynamodb, vendor-recs}
safe_actions:
  - id: vendor-fallback-on
    lane: runtime
    applies_to: vendor-recs
    description: If the errors are vendor-recs, stop calling the vendor and serve cached recommendations.
    appconfig:
      profile: flags
      set: {vendor_fallback: true}
    blast_radius: One customer env. Recommendations may be stale; nothing else changes.
    rollback:
      profile: flags
      set: {vendor_fallback: false}
  - id: rollback-last-appconfig-deployment
    lane: runtime
    applies_to: config-change
    description: If an AppConfig deployment happened in the window, roll back to the previous version.
    blast_radius: One customer env's runtime config returns to its previous version.
    rollback: Redeploy the version that was rolled back.
---

# Dependency errors

**What it means.** More than the threshold of failed calls per minute to Valkey,
DynamoDB or the vendor for one customer env. This alarm is also the **rollback
monitor** for the customer's AppConfig environment: if it fires during the 1-minute
bake of a config deployment, AppConfig rolls that deployment back by itself.

catalog-api degrades instead of failing: Valkey errors fall through to DynamoDB, vendor
errors serve cached recommendations. So this alarm can fire while viewers still get
answers. That's the point: it catches the degradation before the API alarm does.

## Diagnose

1. Dashboard right half, "Dependency errors by customer": which dependency?
2. Logs Insights on `/aws/lambda/sr-<customer>-<env>-catalog-api`:
   `fields @timestamp, msg, dependency, error_type, error | filter level = "WARNING" | sort @timestamp desc | limit 50`
3. Valkey errors: `AuthenticationFailures` / `KeyAuthorizationFailures` on the cache
   (an ACL or IAM change?), the Lambda→Valkey security group rule (6379), CloudTrail
   for `RevokeSecurityGroupIngress` / `ModifyUserGroup` in the window.
4. DynamoDB errors: `AccessDeniedException` in the logs = an IAM change (CloudTrail).
5. Vendor errors: HTTP 5xx from the vendor (outside check as in vendor-latency).

## Act

Only the actions in the front matter, after approval. Anything else (security group,
IAM, code) goes through a PR: re-apply the stack or revert the change.
