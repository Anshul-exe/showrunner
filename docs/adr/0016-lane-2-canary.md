# 0016: Lane 2 canary on the Lambda alias; deploy only an attested zip, by digest

Status: accepted (19/11/2026). Tier 2 item 5 (BRIEF section 0), first part. Amends ADR
0004 (lane 2 moved `live` all at once in the core).

## Context

The design (BRIEF section 7) is: CI builds an arm64 container image, pushes it to ECR by
digest, publishes a Lambda version and runs a **CodeDeploy** canary on alias `live`
(Canary10Percent5Minutes), rolled back by the error and latency alarms.

On this account (Free plan) every CodeDeploy call fails:

```
$ aws deploy list-applications
An error occurred (SubscriptionRequiredException) when calling the ListApplications
operation: The AWS Access Key Id needs a subscription for the service
```

(checked 19/11/2026). CodeDeploy for Lambda has no charge of its own, but the Free plan
doesn't include the service, and upgrading the plan is the owner's call, not this
project's.

A second finding: catalog-api turns its own exceptions into a 500 response, so Lambda's
`Errors` metric stays at 0 for a build that fails requests. An `Errors` alarm would not
have caught S2.

## Decision

1. **The canary is the deploy tool's job** (`tools/deploy/lambda_deploy.py`, used by CI
   and by `./sr deploy catalog-api`). It does what CodeDeploy does for Lambda, with the
   same API underneath: alias `live` stays on the old version with
   `AdditionalVersionWeights` sending 10% of requests to the new one for 5 minutes; the
   rollback alarms are read every 10 s; any in ALARM puts 100% back on the old version,
   otherwise the alias moves to the new version. It refuses to start while a rollback
   alarm is already firing.
2. **The canary config is Terraform's.** Weight, minutes and the rollback alarms
   (`canary-errors`, `api-latency`) are module variables, published to SSM
   `/showrunner/<env>/customers/<c>/canary`; the tool reads them. The **readiness gate**
   refuses a customer stack without one (weight ≤ 50%, alarms that exist in the plan,
   one of them the canary's own).
3. **The canary alarm is the API's 5xx**, not Lambda `Errors`: it counts catalog-api's
   500s and Lambda crashes/timeouts alike (an AWS metric, no custom-metric cost). ≥ 1 a
   minute in 2 of 3 minutes; normal is 0. No notification and no runbook actions: the
   deploy tool is its consumer.
4. **Deploy by digest.** The version is published with `CodeSha256` = the zip's SHA-256
   (Lambda refuses if the uploaded code differs). CI builds the zip once, attests it with
   `actions/attest-build-provenance`, and the deploy job runs
   `gh attestation verify --signer-workflow .../build-deploy.yml --source-ref
   refs/heads/main` before it uses the zip, then passes the attested digest
   (`--expect-sha256`). The deploy job installs nothing (AWS CLI and gh come with the
   runner).
5. **After a rollback, the old code becomes the newest version** (same digest,
   re-published). Terraform points `live` at the newest version on every apply; without
   this, the next apply would move all traffic onto the build the canary just rejected.
6. **No overlap with Terraform.** Each deploy job takes the same concurrency group as that
   customer's apply (`apply-customers/<c>/dev`): an apply mid-canary would reset the
   alias's weights.
7. **The deploy role moves the alias itself** (`lambda:UpdateAlias` on its env's
   functions, `cloudwatch:DescribeAlarms` on its env's alarms) instead of starting
   CodeDeploy; the unused CodeDeploy permissions are removed. That's a bootstrap change
   the owner applies; until then the workflow's deploy job is off (repo variable
   `LANE2_DEPLOY`) and `./sr deploy catalog-api` runs the same canary locally.
8. Every response carries `x-sr-version` (the Lambda version that answered), so the
   split is visible from outside during a canary.

## Not built yet (tier 2 item 5, second part)

- **The container image** (arm64, base pinned by digest, ECR immutable tags, scan on
  push, `image_uri` by digest). Moving catalog-api from a zip to an image replaces the
  function (`package_type` can't change in place), so it gets its own step. The
  provenance and by-digest parts above carry over unchanged: the subject becomes the
  image digest instead of the zip's.
- **S2** (a build that fails 20% of searches, rolled back by the canary). The tooling
  path is tested with a fake AWS (`tests/test_lambda_deploy.py`); the drill on dev isn't
  run yet.

## Consequences

- One component more to trust: if the deploy process is killed mid-canary (runner
  cancelled), `live` stays split 90/10 until the next deploy or `./sr deploy`. An
  exception inside the tool rolls back (tested). CodeDeploy runs that loop as a service.
- The rollback happens as fast as the alarm: 2 of 3 one-minute periods, so ~2-3 minutes
  of a bad build on 10% of requests.
- Free: Lambda aliases and versions cost nothing; one more alarm per customer
  ($0.10/month); attestations are free for public repositories.

## In production

CodeDeploy (or the same tool with a watchdog that resets any alias left split), a bake
period after 100%, per-version error metrics so the canary compares new against old
instead of reading the total, and the deploy gated on an environment with reviewers for
prod.
