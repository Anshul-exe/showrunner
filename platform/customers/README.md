# Customers: one YAML file per customer environment

The golden path (Pillar 1). A new customer environment is a pull request that adds
`platform/customers/<customer>/<env>.yaml`:

```yaml
customer: lumen-kids        # lowercase, hyphens; names every resource sr-<customer>-<env>-*
env: dev
tier: standard              # standard | premium
owner: team-lumen           # a team, never a person (it's a tag)
cost_center: lumen-kids
budget_usd: 10
catalog: tv                 # seed catalog: tv | sports
features: {}                # optional, all off by default: pitr, customer_page, canary
```

What happens:

1. **CI validates the file** against `schema.json` (pytest) and Terraform's variable
   validation.
2. **plan.yml posts a plan comment** for the new stack `customers/<customer>/<env>`
   (its own state, nothing else is planned) with the policy results:
   - **never-create list:** nothing on the Free plan's no-go list (EKS, NAT gateway, RDS,
     customer-managed KMS keys...)
   - **tags:** every resource carries customer, env, owner, cost-center, managed-by
   - **readiness:** the environment can't go live without the alarms that make an
     incident diagnosable: an error alarm for each dependency the service calls
     (Valkey, DynamoDB, the vendor), a latency alarm for each external one, an API
     latency alarm, and every alarm naming a runbook that exists in `ops/runbooks/`
3. **A reviewer approves, merge**, and apply.yml applies only that stack.
4. The incident loop serves it once ops-core is applied again (its act role lists the
   customers' AppConfig apps by ID; until then it can propose but not act).

Policies: `platform/policy/*.rego`, unit-tested with `conftest verify`
(`*_test.rego`). Why one state per customer: [streaming-service README](../modules/streaming-service/README.md).
