# S3: someone removes a security-group rule by hand

Environment: **dev only.** Affects every customer in the env (they share the cache).
Problem it answers: P17 (deployment, access and networking issues, with evidence).

## What breaks

`./sr demo s3 start` revokes the one ingress rule on the cache's security group:
tcp 6379-6380 from the Lambda security group (what `modules/cache` declares). It's the
change someone makes in the console "just for a minute".

Established Valkey connections survive a revoked rule (security groups are stateful:
connection tracking keeps existing flows), so nothing happens at first. catalog-api
recycles a Valkey connection after 120 s (`CACHE_MAX_CONN_AGE_S`); the next connect
fails (~3 s: 1 s per endpoint IP), the circuit breaker opens, and for the next 30 s
requests skip the cache and go straight to DynamoDB. Every skipped call is counted as
a `valkey` error, so the outage stays visible.

**The first drill found that this didn't hold** (18/11/2026): without the breaker every
request paid two failed connects, hit the 6 s Lambda timeout and returned 503 after
8.8 s, and each timeout reset the environment, so the next request was a cold start
that tried again. A cache outage was a full outage, and the timeouts lost the metrics
that would have said so. Fixed with the breaker; see the case file for both runs.

## What you see

| Where | What |
|---|---|
| Dashboard, per dependency | `valkey` errors > 0 and client-side latency ~1 s (connect timeouts) while Valkey's server-side latency stays in microseconds: the path, not the cache |
| Alarms | `sr-<customer>-dev-dependency-errors` (and `api-latency`) for every customer with traffic |
| Incident analysis | H1 `network`: **confirmed** by three checks: the rule is gone (live, vs Terraform), REJECTed flows to 6379/6380 (flow logs, now vs the hour before), Reachability Analyzer blocked at the cache's security group. CloudTrail names who revoked what, when (5-15 min later: CloudTrail's delivery lag) |
| Proposed action | none executed: `reapply-shared-stack` (lane change) is **recommended** with the command, because the fix is a pipeline run, not a runtime switch |

## Fix (lane 3, change)

Re-apply the env's shared stack through the pipeline; the plan shows the one rule it
restores, and nothing else:

    gh workflow run apply.yml --ref main     # a human runs it; the loop never does

## Reset (demo)

    ./sr demo s3 stop      # restores the exact rule (same ports, same description): no drift
    ./sr preflight         # "cache network rule" must be PASS

`./sr demo reset` also restores it.

## Recorded run

[docs/demo/case-files/S3.md](../../docs/demo/case-files/S3.md): the drill on dev with the
real evidence, timings and the analysis the loop would post.
