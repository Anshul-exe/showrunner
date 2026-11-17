# 0002: One AWS account on the Free plan

Date: 18/11/2026. Status: accepted.

## Context

A managed-services platform normally gives each customer (and each environment) its
own AWS account through AWS Organizations, with per-account OIDC roles and SCPs. The
Free plan doesn't support Organizations or Control Tower.

## Decision

Customers share one account. They're separated by:
- **state:** one Terraform state per customer environment (`customers/<name>/<env>`)
- **IAM:** each function's role can only reach its own table, its own AppConfig app,
  and connect to the cache as its own user; every pipeline-created role carries the
  `sr-workload-boundary` permission boundary
- **cache ACLs:** each customer's Valkey user may only touch keys `<customer>:*`
- **tags:** `customer`, `env`, `owner`, `cost-center`, `managed-by` on everything
  (conftest enforces it), for cost allocation
- **CI roles:** dev and prod apply roles can't write each other's state, and deny
  actions on resources tagged with the other env

## Consequences

Isolation is policy-based, not a hard account boundary: a mistake in an IAM policy
could cross customers. Production would use an account per customer environment.

## In production

AWS Organizations, an account per customer per environment (vended by Account Factory
for Terraform or Control Tower), SCPs carrying the never-create list, centralised
CloudTrail and logging accounts, and the same per-env OIDC role module.
