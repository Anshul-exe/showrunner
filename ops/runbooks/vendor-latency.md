---
alarm: vendor-latency
severity: sev2
owner: platform-team
metric: Showrunner/DependencyLatency p95 {customer, env, dependency=vendor-recs}
safe_actions:
  - id: vendor-fallback-on
    lane: runtime
    applies_to: vendor-recs
    description: Stop calling the vendor; serve the last known recommendations from DynamoDB.
    appconfig:
      profile: flags
      set: {vendor_fallback: true}
    blast_radius: One customer env. Recommendations may be up to a few hours old; catalog, titles and search are unaffected.
    rollback:
      profile: flags
      set: {vendor_fallback: false}
---

# Vendor recommendations are slow

**What it means.** Calls from catalog-api to the external recommendations vendor are
slow (p95 above the threshold for 2 of the last 3 minutes). Viewers see slow "more like
this" rows; with enough traffic, Lambda concurrency rises because each request waits
on the vendor.

**Don't be misled by the legacy view.** When the vendor is slow, Lambda duration and
concurrency rise, and so do Valkey's client connections. The service-level dashboard
then *looks* like a cache problem. Check Valkey's own server-side latency: it's in
microseconds. A cache that answers in microseconds can't cause second-long requests.

**When it fires.** Vendor p95 above the threshold in 2 of 3 one-minute periods,
counting only minutes with at least 5 vendor calls (the same floor as api-latency).

## Diagnose (5 minutes)

1. Dashboard `sr-<env>-operations`, right half: "Dependency latency p95 by customer".
   Is it only `vendor-recs`? For one customer or all?
2. Same dashboard: "Valkey server-side latency (µs)". Microseconds = not the cache.
3. Any change in the window? AppConfig deployments for `sr-<customer>-<env>` (a
   `chaos` deployment in dev means a demo is running), recent Lambda versions:
   `aws lambda list-versions-by-function --function-name sr-<customer>-<env>-catalog-api`.
4. Is the vendor slow from outside the VPC too?
   `curl -s -o /dev/null -w '%{time_total}\n' -H 'x-customer: <customer>' "$VENDOR_URL/recs?title=t01"`
   (`VENDOR_URL` is SSM `/showrunner/<env>/shared/vendor_url`). Slow from outside as
   well = the vendor; fast from outside = look at the path (NAT instance, routes).

## Act

- **vendor-fallback-on** (runtime, no deploy): set `vendor_fallback: true` in the
  customer's `flags` profile. Live within ~15-30 s (catalog-api polls every 15 s).
  p95 for the recs route drops to DynamoDB latency.
- Tell the vendor (in production: their status page / support contract).

## Recover

When the vendor is healthy again (its p95 from step 4 is back to tens of ms), roll
back: `vendor_fallback: false`. Watch vendor p95 for 10 minutes.

An alarm that goes OK is not proof: with the fallback on, the vendor gets few calls,
and with no traffic at all every latency alarm goes OK on missing data. Confirm on the
API's p95 **and** its request count for the same minutes (see
[api-latency](api-latency.md), "OK is not recovered").
