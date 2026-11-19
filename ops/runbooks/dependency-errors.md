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
    description: The latest flags deployment caused this (it happened just before the alarm and is still in effect); redeploy the version before it.
    appconfig:
      profile: flags
      rollback: true
    blast_radius: One customer env's flags return to their previous version. Refused if anything was deployed after the cited deployment.
    rollback: Redeploy the rolled-back version (it stays in AppConfig's history).
  - id: reapply-shared-stack
    lane: change
    applies_to: network
    description: A security group, route or NACL in the env's shared network drifted from Terraform (changed by hand). Re-apply the stack through the pipeline; the plan shows exactly what it restores.
    pipeline: "gh workflow run apply.yml --ref main   # re-plans and re-applies every dev stack; shared/<env> restores the rule"
    blast_radius: The env's shared network and cache (all its customers) return to what Terraform declares. Nothing else changes.
    rollback: None needed (it restores the declared state); to keep the hand change, make it a PR instead.
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

Only the actions in the front matter. The loop executes the `runtime` ones after a
human's `/approve`; `reapply-shared-stack` (lane `change`) is recommended in the issue
and run by a human through the pipeline. Anything else (IAM, code) goes through a PR.

Scenario drills: S3 (security-group drift, [ops/scenarios](../scenarios/S3-sg-drift.md))
and S4 (a typo'd vendor host in the flags, [S4](../scenarios/S4-dns-config.md)).

## A fix rolled back by AppConfig (until 19/11/2026)

This alarm used to be the **rollback monitor** of the customer's AppConfig environment.
While it was in ALARM, AppConfig rolled back *every* deployment within seconds,
including the fix for the incident that fired it (issue #17: `s4 stop` was rolled back
in 5 s with "The following CloudWatch monitors are in alarm"). With a 2-of-3-minute
alarm and a 1-minute bake it also never caught a bad change. The monitor is gone
(ADR 0017): a bad config fires this alarm, and the loop proposes rolling it back.

If you ever see "AppConfig rolled back deployment #n" from `sr` or the loop (they
report it as a failure now), check `aws appconfig get-environment ... --query Monitors`:
the environment should have none. A monitor there is drift: re-apply the customer
stack.
