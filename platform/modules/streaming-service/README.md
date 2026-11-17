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
