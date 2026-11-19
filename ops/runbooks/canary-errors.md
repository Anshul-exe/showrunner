---
alarm: canary-errors
severity: sev3
owner: platform-team
incident: false  # the deploy tool consumes it; the incident loop doesn't open an incident
metric: sum of AWS/ApiGateway 5xx {ApiId, Stage} (old and new version together)
safe_actions: []
---

# Canary errors

**What it means.** The customer's API returned 5xx responses in 2 of the last 3
minutes (catalog-api's own 500s, and Lambda crashes or timeouts). Normal is zero. This
alarm exists for the **lane-2 canary** (ADR 0016): during a deploy, 10% of requests go
to the new version for 5 minutes, and if this alarm (or `api-latency`) fires in that
window, the deploy tool puts all traffic back on the old version by itself.

**When it fires during a deploy (S2).** The platform has already handled it: the deploy
output says `rolled-back (alarm: ...)`, alias `live` points at the old version with no
routing weights, and the old code was re-published as the newest version (so a later
Terraform apply, which points `live` at the newest version, can't bring the bad build
back). Nothing to approve.

**When it fires with no deploy running.** The errors come from the version everyone
is on. Look at what else fired: `dependency-errors` (a dependency failing hard enough to
turn into 500s), or the S3 drill (cache timeouts before the circuit breaker). Lambda
timeouts count too (API Gateway answers 503/504).

## Diagnose

1. Was a deploy running? The `build-deploy` workflow's latest run, or
   `aws lambda get-alias --function-name sr-<customer>-<env>-catalog-api --name live`
   (`RoutingConfig` is set only mid-canary).
2. Which version failed? Responses carry `x-sr-version`; in the function's log group,
   each request logs `status`, and the log stream name carries the version
   (`.../[<version>]...`).
3. What failed? The function's log group, `filter level = "ERROR"`.

## Fix

A bad build: fix it on a branch and deploy again (lane 2). Nothing to undo: the canary
already put `live` back. If a deploy was killed mid-canary (`RoutingConfig` still set),
re-run `./sr deploy catalog-api`; it starts from the version `live` points at.
