# 0015: Incident checks need history; every change in the window is a hypothesis

Status: accepted (19/11/2026). Amends the incident loop's verifier (pillar 1).

## Context

Issue #7 (18/11/2026 21:06 UTC) was a false alarm: two cold-start requests right after a
catalog-api deploy (v4, 2 minutes before the alarm) fired `api-latency` on dev. The loop:

- **confirmed** `vendor-recs` from one slow call (1,964 ms) against the dependency's
  *expected* p95 (250 ms), because there was no history before the alarm;
- let the model file the deploy under `lambda`, which has no check, so the most likely
  cause was shown as "not checkable" and the vendor won;
- proposed `vendor-fallback-on`, a workaround for a problem that didn't exist. Nobody
  approved it; it escalated after 30 minutes.

The alarm side was fixed separately (latency alarms ignore minutes with fewer than 5
calls). This ADR is the analysis side: the loop must not reach a confident wrong answer
from thin data, and must never hide a change that happened just before the alarm.

## Decision

1. **Sample counts are evidence.** The evidence step reads `SampleCount` next to every
   p95 (catalog-api's dependency calls and API Gateway), and records how many minutes of
   observed history came before the alarm. The summaries say it: "1,964 ms in the last
   3 min (1 calls)".
2. **Not enough history is its own verdict.** A dependency check with fewer than 10
   calls in the 3 "now" minutes, or fewer than 3 observed minutes before the alarm (only
   the documented expected p95), returns `not-enough-history`. It is never `confirmed`,
   the verifier model can't change it (like `not-checkable`), and no action is proposed
   for it. Errors are counted calls, so a dependency with 5 or more errors is still
   judged on them (S3: the cache breaker turns each skipped call into an error at ~1 ms).
   The thresholds match the alarm floor (5 calls a minute, 2 of 3 minutes) and the
   baseline rule (3 minutes).
3. **Every change in the window is a hypothesis, on both triage paths.** If the 15
   minutes before the alarm contain an AppConfig `flags` deployment, a catalog-api
   version, or a Terraform apply from CI (CloudTrail sessions `apply-<run id>`), code
   makes sure a `config-change` hypothesis exists, citing that evidence. A model
   hypothesis whose lead is the change (it cites the change evidence, e.g. `lambda`
   citing the deploy) becomes that hypothesis, keeping the model's words; otherwise one
   is added on top of the model's three, so it's never crowded out. The check then
   confirms it (in effect: live version, flags not at defaults, an apply) or refutes it
   with the reason (reverted, not live).
4. **An AppConfig rollback is only proposed for a flags change that is in effect**, never
   for a code deploy or an apply.

Replayed on #7's stored evidence and the model's real answer (19/11/2026): H1
`vendor-recs` **not enough history**, H2 `config-change` **confirmed** ("live code v4 was
published 2 min before the alarm", E9), no action proposed. Tests:
`tests/ops/test_history.py`.

## Consequences

- An alarm on very little traffic now ends with "not enough history" and a human, not a
  workaround. That's the point; in a demo it means S1 needs its warm-up traffic (the
  recorded S1 run had 9 minutes of history).
- One more hypothesis can appear (up to 4). It's always cited and always checked.
- Terraform applies show up only once CloudTrail has delivered them (3-15 minutes): an
  apply in the last few minutes before an alarm can still be missed. The Lambda version
  and AppConfig deployment lists are read directly and have no lag.

## When we'd decide differently

With real production traffic the floor would come from the traffic itself (a share of
the normal request rate, per route), and the baseline from the same hour last week
rather than a fixed expected value. A change hypothesis would also cover feature-flag
services and database migrations, each with its own "in effect?" check.
