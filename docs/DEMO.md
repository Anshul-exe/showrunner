# Demo script (about 9 minutes, `dev` only)

Everything runs against `dev`. Nothing is deployed or rebuilt during the demo: the one
live, long-running piece is scenario S1, and it runs in the background. Every segment
has a recorded fallback; if something is slow live, open the recording.

## Before (10 minutes ahead)

```bash
export AWS_PROFILE=showrunner AWS_REGION=us-east-1   # sr defaults to this profile anyway
./sr preflight                 # must say READY (WARN lines are fine: they say why)
./sr demo reset                # only if preflight says config is not at defaults
./sr demo traffic --minutes 25 --rps 3 &    # warm-up: the incident baseline needs ~5 min of history
                               # (its last line says why it stopped: time up, Ctrl-C, signal, error)
./sr demo watch                # second terminal: per-dependency p95 every 15 s
```

Open in one browser window: the README, the CloudWatch dashboard (`sr-dev-operations`), the
repo's Issues filtered by `label:incident`, and PR #3 (plan comments).

## Segments

| When | Segment | Show | Fallback |
|---|---|---|---|
| 0:00 | Frame | README: the four bullets, the diagram, the measured timings | - |
| 1:00 | Start S1 | `./sr demo s1 start`: "the vendor gets 1.5 s slower for acme-tv; this pages in ~1-2 minutes" | [S1 recording](demo/S1.md) |
| 1:30 | Self-service | a fourth customer, live: the commands below open a PR with one 10-line file; while CI plans it, show `acme-tv/dev.yaml` -> one module; then the plan comment: 61 to add, policy checks incl. **readiness** (every dependency alarmed, every alarm has a runbook) and the apply-role access check | [PR #5](https://github.com/Anshul-exe/showrunner/pull/5): the third customer, lumen-kids, added the same way (plan comments per stack) |
| 3:00 | Supply chain | PR #4: a "routine" dependency whose postinstall hunts for credentials; the `supply-chain` check: scripts off, then run on purpose: finds nothing, harden-runner blocks `exfil.example.com` | [recording](demo/supply-chain.md) |
| 3:45 | Review agents | the seeded PR (open it before the demo: [commands](demo/review-agents.md#run-it-live)): one comment, **1 confirmed** (`s3:*` on `*`), **2 refuted by code checks** (an action with no resource type; a Deny), collapsed with the reason; the gate fails; "the model enumerates, code judges" | [recorded run](demo/review-agents.md), 18/11/2026 |
| 4:30 | Incident | the issue: evidence table, H1 vendor confirmed, **the cache refuted with Valkey's µs**, A1 with the exact diff, blast radius and rollback; comment `/approve A1`; `./sr demo watch` p95 falls with no deploy; the audit rows. Expect (18/11/2026): proposal 13 s after the alarm, flag live ~1 min after `/approve`, recovered ~3 min later | [issue #6](https://github.com/Anshul-exe/showrunner/issues/6): the whole loop on 18/11/2026, alarm -> recovered 334 s, PIR at the end; [timeline](demo/incident.md) |
| 7:00 | Network case file (S3) | recorded, not live (CloudTrail takes ~3-4 min): [case file](demo/case-files/S3.md): a rule revoked by hand; H1 `network` **confirmed by independent checks** (the live rule vs Terraform, Reachability Analyzer blocked at the cache's SG, flow-log REJECTs) and CloudTrail names who; the fix is a pipeline run, so the loop **recommends** it and executes nothing. Then the honest part: the first drill found that a cache outage was a full outage (503 after 8.8 s); a circuit breaker fixed it | the same case file (two runs, 18/11/2026) |
| 8:00 | Roadmap | [ROADMAP.md](ROADMAP.md): tiers 2-3 against the three six-month goals | - |

After: `./sr demo s1 stop` (or `./sr demo reset`), then `./sr preflight`.

## The live customer PR (segment 1:30)

```bash
git switch -c customer/harbor-news-dev
mkdir -p platform/customers/harbor-news
cat > platform/customers/harbor-news/dev.yaml <<'YAML'
customer: harbor-news
env: dev
tier: standard
owner: team-harbor
cost_center: harbor-news
budget_usd: 10
catalog: tv
features: {}
YAML
git add platform/customers/harbor-news && git commit -m "feat(customers): harbor-news dev"
git push -u origin customer/harbor-news-dev
gh pr create --fill --label demo
```

Merging applies only that stack: lumen-kids' apply job took 3 min 4 s in CI
(18/11/2026 09:22:59–09:26:03 UTC, run 44275074495; first requests 200, recs from the vendor). Leave it unmerged
after the demo if the 3 extra alarms ($0.10 each per month) aren't wanted.

## What to say when it differs

- **"Model unavailable" in the triage heading:** Bedrock failed or throttled (it's
  enabled since 18/11/2026; `sr preflight` checks it). The loop fell back to
  deterministic rules, and the verifier's verdicts are numeric checks either way.
- **The review comment differs from the recording:** the model may word findings
  differently or skip a look-alike; the verdicts come from the checks, so whatever it
  reports gets the same verdict. "**Not reviewed by the model: daily budget reached**"
  means the 40-a-day cap was used (the rules result stands); "**Not reviewed by the
  model:** <reason>" with a failed gate is an infrastructure error (fail closed, ADR
  0013): fix it, re-run failed jobs.
- **"expected p95 (only N min of history)":** traffic started recently; the baseline is
  the documented normal latency instead of a median that might include the problem.
- **The vendor alarm stays red after the fix:** with the fallback on, the vendor metric
  mostly stops, so the alarm has little data to clear on. Recovery is checked on the
  API p95, which is what users see.
- **Traffic capped at 4 rps:** the account's Lambda concurrency quota is 10 (new
  account); Little's law, see `tools/sr/traffic.py`.
