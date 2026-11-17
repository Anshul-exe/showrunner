---
alarm: api-latency
severity: sev3
owner: platform-team
metric: AWS/ApiGateway Latency p95 {ApiId, Stage=$default}
safe_actions:
  - id: vendor-fallback-on
    lane: runtime
    applies_to: vendor-recs
    description: Only if per-dependency metrics show the vendor is the slow dependency.
    appconfig:
      profile: flags
      set: {vendor_fallback: true}
    blast_radius: One customer env. Recommendations may be stale.
    rollback:
      profile: flags
      set: {vendor_fallback: false}
---

# API latency (what viewers feel)

**What it means.** The customer's API p95 is above the threshold. This alarm says
*that* viewers are affected, not *why*. Never act on it alone: find the dependency.

## Diagnose

1. Dashboard right half: which dependency's p95 moved at the same time?
   - only `vendor-recs` → follow [vendor-latency](vendor-latency.md)
   - `valkey` in **milliseconds** (client side) while Valkey's server-side latency is
     still microseconds → the path to the cache (network, connection churn), not the
     cache itself
   - nothing moved → cold starts or throttling: Lambda `Throttles`, account
     `ConcurrentExecutions` vs the quota (10 on a new account), API Gateway 429s
2. A deploy or config change in the window? (Lambda versions, AppConfig deployments.)

## Act

Only the actions in the front matter, after approval, and only when step 1 points at
the vendor.
