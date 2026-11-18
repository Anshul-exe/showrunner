# Demo script (about 9 minutes, `dev` only)

Everything runs against `dev`. Nothing is deployed or rebuilt during the demo: the one
live, long-running piece is scenario S1, and it runs in the background. Every segment
has a recorded fallback; if something is slow live, open the recording.

## Before (10 minutes ahead)

```bash
./sr preflight                 # must say READY (WARN lines are fine: they say why)
./sr demo reset                # only if preflight says config is not at defaults
./sr demo traffic --minutes 25 --rps 3 &    # warm-up: the incident baseline needs ~5 min of history
./sr demo watch                # second terminal: per-dependency p95 every 15 s
```

Open in one browser window: the README, the CloudWatch dashboard (`sr-dev-operations`), the
repo's Issues filtered by `label:incident`, and PR #3 (plan comments).

## Segments

| When | Segment | Show | Fallback |
|---|---|---|---|
| 0:00 | Frame | README: the four bullets, the diagram, the measured timings | - |
| 1:00 | Start S1 | `./sr demo s1 start`: "the vendor gets 1.5 s slower for acme-tv; this pages in ~1-2 minutes" | [S1 recording](demo/S1.md) |
| 1:30 | Self-service | `platform/customers/acme-tv/dev.yaml` -> one module; a PR's plan comment per stack with policy checks | PR #3 comments |
| 3:00 | Supply chain | PR #4: a "routine" dependency whose postinstall hunts for credentials; the `supply-chain` check: scripts off, then run on purpose: finds nothing, harden-runner blocks `exfil.example.com` | [recording](demo/supply-chain.md) |
| 4:30 | Incident | the issue: evidence table, H1 vendor confirmed, **the cache refuted with Valkey's µs**, A1 with the exact diff, blast radius and rollback; comment `/approve A1`; `./sr demo watch` p95 falls with no deploy; the audit rows | issue #2 (17/11/2026 run), [S1 recording](demo/S1.md) |
| 8:00 | Roadmap | [ROADMAP.md](ROADMAP.md): tiers 2-3 against the three six-month goals | - |

After: `./sr demo s1 stop` (or `./sr demo reset`), then `./sr preflight`.

## What to say when it differs

- **"Model unavailable" in the triage heading:** Bedrock isn't enabled on this account
  yet; triage ran on deterministic rules, and the verifier's verdicts are numeric
  checks either way.
- **"expected p95 (only N min of history)":** traffic started recently; the baseline is
  the documented normal latency instead of a median that might include the problem.
- **The vendor alarm stays red after the fix:** with the fallback on, the vendor metric
  mostly stops, so the alarm has little data to clear on. Recovery is checked on the
  API p95, which is what users see.
- **Traffic capped at 4 rps:** the account's Lambda concurrency quota is 10 (new
  account); Little's law, see `tools/sr/traffic.py`.
