# 0010: ops-core reads two dev outputs with the prod apply role

Date: 18/11/2026. Status: accepted.

## Context

ops-core (the incident loop) is one stack that serves every environment. For each env
it reads two non-secret outputs of that env's shared stack from SSM:
`/showrunner/<env>/shared/appconfig_strategy_id` and `.../alerts_topic_arn`.

It's applied by `showrunner-ci-apply-prod` (behind the `prod` reviewer). That role has
`DenyOtherEnvResources`: deny everything on resources tagged `env=dev`, which is how one
account keeps a prod apply off dev resources (ADR 0002). The two parameters are tagged
`env=dev`, so the first CI apply of ops-core failed at plan time (run 44205791825,
18/11/2026: `explicit deny in an identity-based policy`). It had only ever been applied
from an admin laptop. The PR plan didn't catch it, because PR plans run as the
read-only plan role, which has no such deny.

## Options

1. **Carve the two parameters out of the deny, read-only.** Chosen.
2. **A separate ops-core apply role.** Cleaner on paper: the prod role would lose
   ops-core, and the ops role could do only ops-core. In practice both roles would
   trust the same OIDC subject (`environment:prod` from `apply.yml` on main), so either
   job could assume either role. Making the split real means a third GitHub environment
   with its own reviewer, a new role and workflow changes, all to protect two
   non-secret IDs. In production this split comes for free with an account per
   environment, where these reads become cross-account (ADR 0002).

## Decision

In `platform/bootstrap/roles.tf`, the prod apply role's env entry lists
`other_env_reads` (the two ARNs, exact, no wildcards):
- `DenyOtherEnvResources` uses `NotResource` on that list, so the tag deny still covers
  every other `env=dev` resource
- `OtherEnvOutputsReadOnly` denies every action except `ssm:GetParameter(s)` on them, so
  the role can't overwrite or delete them (PowerUserAccess would otherwise allow that)

Nothing under `/showrunner/secret/` is involved, and the parameters are `String`, so no
decryption either.

Verified with the IAM policy simulator (`platform/bootstrap/tests/iam-guardrails.sh`,
37/37): GetParameter on the two is allowed; PutParameter on them, GetParameter on any
other dev parameter, and dev role reads of prod outputs are all denied.

## Catching it in the PR next time

`tools/ci/access_check.py` runs in every PR plan job. It takes the SSM reads from the
plan's data sources and asks the policy simulator whether the role that will **apply**
that stack may make each one, passing each parameter's tags as `aws:ResourceTag/*`
context (the simulator doesn't look tags up itself, and a tag condition was the cause
here). A deny fails the plan job and is listed in the plan comment. Negative control on
18/11/2026, before the fix: it reported exactly the two parameters CI had failed on, as
`explicitDeny`; after the fix, 4/4 reads allowed. In CI, first run on [PR #5](https://github.com/Anshul-exe/showrunner/pull/5):
every stack's plan comment carries the line, ops-core 4/4 allowed.

## When we'd decide differently

With a prod env (tier 3) ops-core also reads prod outputs, which needs nothing new.
If ops-core starts reading more than a handful of dev values, or anything sensitive,
move to option 2 with its own GitHub environment.
