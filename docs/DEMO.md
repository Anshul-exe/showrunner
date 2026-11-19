# Demo script (`dev` only): 6, 12 or 30 minutes

Everything runs against `dev`. Nothing is deployed or rebuilt during the demo: the one
live, long-running piece is scenario S1, and it runs in the background. Every segment
has a recorded fallback; if something is slow live, open the recording. Only what works
today is in these runs (19/11/2026); what isn't built is in the roadmap segment.

## Before (10 minutes ahead)

```bash
export AWS_PROFILE=showrunner AWS_REGION=us-east-1   # sr defaults to this profile anyway
./sr preflight                 # must say READY (WARN lines are fine: they say why)
./sr demo reset                # only if preflight says config is not at defaults
./sr demo traffic --minutes 40 --rps 3 &    # warm-up: the incident baseline needs ~5 min of history
                               # 40 min: it must outlast the alarm, your /approve and the
                               # loop's 10-minute recovery check (issue #15: 25 min ran out)
                               # (its last line says why it stopped: time up, Ctrl-C, signal, error)
./sr demo watch                # second terminal: per-dependency p95 every 15 s
```

S1 needs a quiet dev: start it only if nothing was applied or deployed to dev in the last
15 minutes (otherwise the loop rightly names that change as a hypothesis). It also needs
viewers until the end: `./sr demo s1 start` warns if no traffic is running or it ends
within 20 minutes. Restart the traffic then; with no requests the loop can only say
"can't verify: no traffic" ([issue #15](https://github.com/Anshul-exe/showrunner/issues/15)).

Open in one browser window: the README, the CloudWatch dashboard (`sr-dev-operations`),
the repo's Issues filtered by `label:incident`, PR #5 (plan comments), PR #4 (supply
chain) and PR #10 (review agents).

## Segments (used by the runs below)

| Id | Segment | Show | Fallback |
|---|---|---|---|
| F | Frame | README: the four bullets, the diagram, the measured timings | - |
| S1 | Start S1 | `./sr demo s1 start`: "the vendor gets 1.5 s slower for acme-tv; this pages in ~1-2 minutes" | [S1 recording](demo/S1.md) |
| C | Self-service | a fourth customer, live: the commands below open a PR with one 10-line file; while CI plans it, show `acme-tv/dev.yaml` -> one module; then the plan comment: resources to add, policy checks incl. **readiness** (every dependency alarmed, every alarm has a runbook, a canary config) and the apply-role access check | [PR #5](https://github.com/Anshul-exe/showrunner/pull/5): the third customer, lumen-kids, added the same way (plan comments per stack) |
| SC | Supply chain | PR #4: a "routine" dependency whose postinstall hunts for credentials; the `supply-chain` check: scripts off, then run on purpose: finds nothing, harden-runner blocks `exfil.example.com` | [recording](demo/supply-chain.md) |
| RA | Review agents | PR #10: one comment, **1 confirmed** (`s3:*` on `*`), **2 refuted by code checks** (an action with no resource type; a Deny), collapsed with the reason; the required check fails; "the model enumerates, code judges". Then the override: `accept-risk` + a reason comment, the re-run passes, the audit row | [recorded run](demo/review-agents.md) (18/11/2026: gate failed in 23 s, re-run passed in 30 s, $0.00018) |
| I | Incident | the issue: evidence table, H1 vendor confirmed, **the cache refuted with Valkey's µs**, A1 with the exact diff, blast radius and rollback; comment `/approve A1`; `./sr demo watch` p95 falls with no deploy; the audit rows. Expect (18/11/2026): proposal 13 s after the alarm, flag live ~1 min after `/approve`, recovered ~3 min later | [issue #6](https://github.com/Anshul-exe/showrunner/issues/6): the whole loop on 18/11/2026, alarm -> recovered 334 s, PIR at the end; [timeline](demo/incident.md) |
| FA | The false alarm | recorded: two cold-start requests after a deploy fired a latency alarm; the loop confirmed a slow vendor from one call. The fix ([ADR 0015](adr/0015-incident-checks-need-history.md)): sample counts are evidence, "not enough history" is a verdict, every change in the window is a hypothesis | the ADR and the replay of the stored evidence in it |
| N | Network case file (S3) | recorded, not live (CloudTrail takes ~3-4 min): [case file](demo/case-files/S3.md): a rule revoked by hand; H1 `network` **confirmed by independent checks** (the live rule vs Terraform, Reachability Analyzer blocked at the cache's SG, flow-log REJECTs) and CloudTrail names who; the fix is a pipeline run, so the loop **recommends** it and executes nothing. Then the honest part: the first drill found that a cache outage was a full outage (503 after 8.8 s); a circuit breaker fixed it | the same case file (two runs, 18/11/2026) |
| D | Safe deploy | [the canary record](demo/canary.md): an attested zip from `main` (`gh attestation verify` output), 10% for 5 minutes on each customer, the alias mid-canary, promoted after 320-340 s; why not CodeDeploy (not on the Free plan) and the rollback bug it avoids (Terraform would have moved `live` onto a rejected build) | the same record (19/11/2026); recorded only: a deploy takes ~6 minutes |
| $ | Cost | [COST.md](COST.md): what runs all month and why (NAT instance, pooled cache), what's never created | - |
| R | Roadmap | [ROADMAP.md](ROADMAP.md): tiers 2-3 against the three six-month goals; not built yet: the container image, S2, the customer page (blocked by an AWS account check) | - |

## 6 minutes

| When | Segment |
|---|---|
| 0:00 | F (1 min) |
| 1:00 | S1 start, then RA (1.5 min) while it pages |
| 2:30 | I (3 min): the issue, `/approve A1`, the p95 falling |
| 5:30 | one sentence each on N and SC ("recorded; happy to open them"), stop |

## 12 minutes

| When | Segment |
|---|---|
| 0:00 | F |
| 1:00 | S1 start |
| 1:30 | C (open the PR; come back to the plan comment at 6:30) |
| 3:00 | SC |
| 3:45 | RA |
| 4:30 | I |
| 7:00 | C's plan comment, then N |
| 9:00 | FA |
| 10:30 | R, then questions |

## 30 minutes

| When | Segment |
|---|---|
| 0:00 | F, with the problem each pillar answers (3 min) |
| 3:00 | S1 start |
| 3:30 | C: open the PR, walk the module and the policies (readiness, never-create, tags) while CI plans |
| 8:00 | SC: both layers, and the harden-runner log |
| 11:00 | RA: the comment, the refuted list and why each was refuted, the override and its audit row, the ruleset |
| 15:00 | I: the issue end to end, `/approve A1`, recovery, the PIR, the audit rows |
| 20:00 | C's plan comment: what the apply-role access check is for (ADR 0010) |
| 21:30 | FA: the false alarm I caused and the fix |
| 23:30 | N: the S3 case file and the circuit breaker |
| 25:30 | D: the canary record |
| 27:00 | $ and R |
| 28:30 | questions |

After: `./sr demo s1 stop` (or `./sr demo reset`), then `./sr preflight`.

## The live customer PR (segment C)

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
- **"Can't verify recovery: no traffic since HH:MM":** the demo traffic ended before the
  loop could measure the fix. Not a failure: start the traffic again and show the p95 in
  `./sr demo watch` (issue #15 is this exact case, measured by hand: 28-41 ms).
- **The vendor alarm stays red after the fix:** with the fallback on, the vendor metric
  mostly stops, so the alarm has little data to clear on. Recovery is checked on the
  API p95, which is what users see.
- **Traffic capped at 4 rps:** only while the account's Lambda concurrency quota is 10
  (new accounts); Little's law, see `tools/sr/traffic.py`. The quota is 400 since
  19/11/2026 (`sr preflight`), so the cap is 20 rps.
