# 0017: No AppConfig rollback monitor; the incident loop is the rollback path

Status: accepted (owner, 19/11/2026). Amends ADR 0004 (lane 1).

## Context

Lane 1 (runtime config) deployed through AppConfig with the customer's
`dependency-errors` alarm as the environment's rollback monitor and a 1-minute bake.
Scenario S4 on dev (issue #17, 19/11/2026) showed two problems:

1. **It blocked the fix.** The alarm that fires during the incident *is* the monitor.
   While it was in ALARM, AppConfig rolled back the reset deployment (#22) in 5 seconds:
   "The following CloudWatch monitors are in alarm". Every config fix for an
   errors incident would have gone the same way, including the loop's own approved
   `rollback-last-appconfig-deployment` and `vendor-fallback-on`. S1 never showed it,
   because S1's alarm (latency) isn't the monitor.
2. **It never protected anything.** The bad change (#21) completed. The alarm needs 2 of
   3 one-minute periods, so it can't fire inside a 1-minute bake.

Also found: the deploy code treated `ROLLED_BACK` as "live", so `sr` printed success and
the loop would have posted "executed". Fixed separately (a rolled-back deployment now
raises; the loop escalates "the approved action failed", with AppConfig's reason).

## Options

| Option | For | Against |
|---|---|---|
| **Drop the monitor** (chosen) | the fix always lands; nothing to bypass; one in-place change per customer stack and two IAM resources removed | no automatic rollback of a bad config |
| Keep it, 5-minute bake, and the act step detaches/reattaches the monitor around an approved fix | auto-rollback for routine changes | the act role gets `appconfig:UpdateEnvironment` + `iam:PassRole`: the agent's role could switch off a guardrail; half a day of work; ops-core and bootstrap changes before the round |
| Keep it as is | none | S4 can't be fixed by the loop |

## Decision

No rollback monitor on the customers' AppConfig environments. A bad runtime change
fires the customer's alarm like any other fault; the incident loop names the change
(H `config-change`, with the deployment number and, for a host, whether it resolves)
and proposes rolling exactly that deployment back. A human approves it. The strategy
keeps its 1-minute bake, which now only spaces deployments out.

## Consequences

- Time to undo a bad config = alarm (~2-3 min) + analysis (~20 s) + a human's
  `/approve`, instead of an automatic rollback that, as built, never happened.
- `sr` and the loop report a rolled-back deployment as a failure, so a monitor added
  back by hand (drift) shows up instead of hiding.
- In production: monitors that watch for regressions *caused by this deployment*
  (per-version or per-config metrics, a longer bake), with incident fixes deployed
  through a path that doesn't share the incident's alarm.
