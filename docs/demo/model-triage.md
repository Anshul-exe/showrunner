# Model triage, first run (18/11/2026)

Bedrock answered on this account for the first time on 18/11/2026. To show the model
path working, the deployed `sr-ops-incident-triage` Lambda was invoked **once**
(synchronous, `AWS_MAX_ATTEMPTS=1`, so at most one model call) at 09:10:09 UTC. Input:
the exact state the loop's Triage step received for issue
[#1](https://github.com/Anshul-exe/showrunner/issues/1) on 17/11/2026, taken from the
Step Functions execution history. The incident ID was changed to
`modelcheck-20261118-acme-tv-dev-20261117T2311`, so the audit row is separate from
incident #1's trail.

| | |
|---|---|
| mode | `model` (no fallback) |
| model | `amazon.nova-lite-v1:0` (in-region ID, ADR 0012) |
| tokens | 1,265 in, 222 out |
| cost | $0.000129 (at $0.00006 / $0.00024 per 1K tokens) |
| instruction file | `ops/agents/triage.md`, SHA-256 `e33c41e9…33a0` (in the audit row) |

## What it said, against what the rules said on 17/11

Issue #1's evidence is the **misleading** set (its baseline was one cold-start minute;
see [incident.md](incident.md)): vendor p95 1,692 ms "vs 1,366 ms before".

| | deterministic rules (17/11, Bedrock blocked) | Nova Lite (18/11, same evidence) |
|---|---|---|
| H1 | `config-change` (E7, E9), no action | `vendor-recs` (E1, E5), action `vendor-fallback-on` |
| H2 | none | `config-change` (E7, E8): "toggling of the `vendor_fallback` flag" |
| H3 | none | `lambda` (E2): cold starts or concurrency |

The model ranked the right component first on evidence where the old rules didn't. It
also kept the stale config change as H2, although E8 shows the flags back at their
defaults. That's what the verifier is for: since the 17/11 fix, a change counts only if
it's within 15 minutes before the alarm **and** still in effect, so H2 would be refuted
by its checks. The verifier wasn't run here (one call only), so that part is reasoning,
not a measurement.

Not trusted blindly either way: the loop drops hypotheses whose cites aren't real
evidence IDs or whose action isn't in the runbook (`sanitize_hypotheses`), the verifier
decides the verdicts with numeric checks, and nothing runs without `/approve`.

## Next

An eval set: the S1 evidence from both runs plus later incidents, scored for "right
component first" on the rules and the model (ROADMAP).
