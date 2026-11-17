# Architecture decision records

Short records of decisions that someone would otherwise ask "why?" about. Format:
context, decision, consequences, and when we'd decide differently.

| # | Decision | Status |
|---|---|---|
| [0001](0001-serverless-core-no-eks.md) | Serverless core plus a small VPC tier; no EKS | accepted |
| [0002](0002-one-account.md) | One AWS account on the Free plan | accepted |
| [0003](0003-nat-instance.md) | NAT instance, not NAT gateway | accepted |
| [0004](0004-three-change-lanes.md) | Three change lanes: runtime config, app code, infrastructure | accepted |
| 0005 | Agents propose, humans approve | day 2 |
| [0006](0006-shared-cache-iam-auth.md) | One Valkey cache per env, per-customer IAM-authenticated ACL users | accepted |
| [0008](0008-approver-role-scope.md) | The approver role can't be scoped to one state machine | accepted |
