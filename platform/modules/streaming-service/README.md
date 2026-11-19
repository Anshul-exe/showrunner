# streaming-service

One customer's streaming backend in one environment, from a ~10-line YAML spec
(`platform/customers/<name>/<env>.yaml`, validated against `schema.json`). The
golden path: adding a customer is one YAML file and one Terraform stack.

What it creates:
- DynamoDB table `sr-<c>-<env>-catalog` (on-demand), seeded with 12 titles
- a Valkey user `sr-<c>-<env>` (IAM auth, keys `<c>:*` only) in the env's cache
- `catalog-api` (lambda-function module) in the env's private subnets
- an HTTP API (throttled: burst 20, rate 10/s) with access logs (no client IPs)
- an AppConfig application with `flags` and `chaos` profiles, both JSON-schema
  validated, and an environment whose monitor is the dependency-errors alarm
- three alarms, each linked to its runbook in `ops/runbooks/`:
  `vendor-latency` (p95 > 1 s), `dependency-errors` (> 5 in 3 min), `api-latency`
  (API Gateway p95 > 1 s); all to the env's SNS topic
- SSM parameters `/showrunner/<env>/customers/<c>/*` for `sr` and other stacks

It reads the shared stack's outputs from `/showrunner/<env>/shared/*` (SSM), not from
remote state, so the customer role never needs read access to shared's state.

**Why one state per customer:** a change to acme-tv can't touch nordic-sports, plans
stay small (~22 s), and the blast radius of a bad apply is one customer.

## Customer page (`features.customer_page`, tier 2; off by default)

`site.tf` + `site/`: a private S3 bucket behind one CloudFront distribution.
`/*` serves the page (S3 via Origin Access Control: only this distribution can read the
bucket); `/api/*` goes to the customer's HTTP API with `/api` stripped by a CloudFront
Function and caching disabled, so the page shows live latency and which source served
the recommendations (`vendor`, or `fallback` during S1). Strict CSP (`'self'` only),
HSTS, `nosniff`, `DENY` framing. Page files carry `max-age=300`, so a change shows
within 5 minutes without an invalidation.

**Why CloudFront in front of the API too:** one origin for the page and its API (no
CORS), and the viewer's path is the real one: edge -> API -> Lambda -> dependencies.

**Status (18/11/2026):** planned and policy-checked (16 resources, conftest + checkov
pass); the first apply was refused because new accounts must be verified before
creating CloudFront resources (a support case). Turned back off until then.

## Canary (`canary.tf`, tier 2, ADR 0016)

New catalog-api code reaches 10% of requests for 5 minutes before everyone
(`var.canary`). Terraform owns the config: the `canary-errors` alarm (API 5xx) and the
`/showrunner/<env>/customers/<c>/canary` parameter (weight, minutes, rollback alarms),
which `tools/deploy/lambda_deploy.py` reads. The readiness gate refuses a stack without
it. Why not CodeDeploy: it isn't on the AWS Free plan; the tool drives the same Lambda
alias weights CodeDeploy would.
