# S1: the vendor is slow, and the old dashboard blames the cache

Environment: **dev only.** Customer: acme-tv. nordic-sports is the control group.

## What breaks

`./sr demo s1 start` deploys acme-tv's AppConfig `chaos` profile with
`vendor_latency_ms = 1500`. The vendor mock adds 1.5 s to every acme-tv call. Nothing
else changes: no deploy, no Terraform.

## What you see

| Where | What |
|---|---|
| Dashboard, left half ("legacy" service view) | acme-tv API latency up. Nothing says why. The cache is right there in the request path: easy to blame. |
| Dashboard, right half (per dependency) | `vendor-recs` p95 ~1.6 s; `valkey` and `dynamodb` flat (milliseconds); Valkey's server-side latency in microseconds |
| Alarms | `sr-acme-tv-dev-vendor-latency` -> ALARM (p95 > 1 s, 2 of 3 minutes), SNS email with the runbook link. nordic-sports stays OK |

## Mitigation (lane 1, runtime)

The runbook's safe action `vendor-fallback-on`:

    ./sr config set acme-tv flags vendor_fallback=true

catalog-api serves recommendations cached in DynamoDB (`source: fallback` in the logs)
instead of calling the vendor. Rollback: `vendor_fallback=false`.

## Reset

    ./sr demo s1 stop      # chaos off and flags back to defaults

## Automated check

`tests/e2e/s1_runtime_lane.sh` runs the whole thing with traffic and prints the
timings (alarm, flag live, first fallback request, recs latency after). Measured
results are in `docs/demo/S1.md`.

Note: the runtime deployment strategy bakes for 1 minute and AppConfig allows one
deployment per environment at a time, so a second change right after the first waits
up to a minute. `sr config set` waits for it and says so.
