# 0001: Serverless core plus a small VPC tier; no EKS

Date: 17/11/2026 (recorded 18/11/2026). Status: accepted.

## Context

The platform runs two (soon more) customers' catalog APIs, always on, on an AWS
Free-plan account: credits only, and the account closes when they run out. The EKS
control plane alone is $0.10/hour, about $73/month, before any nodes, load balancer or
NAT. That would use the credits in one to two months.

## Decision

- Request path on Lambda (Python 3.12, arm64) behind API Gateway HTTP APIs.
- A real network tier anyway, because the job is about networks too: a VPC with
  private subnets for the Lambdas and the cache, a NAT instance (ADR 0003), gateway
  endpoints, flow logs, and ElastiCache Serverless for Valkey with TLS.
- No EKS, no ECS, no load balancers. The never-create policy (conftest) and the CI
  permission boundary both deny them.

## Consequences

- Near-zero idle cost for compute; the always-on cost is the NAT instance and the
  cache (~$16/month, see docs/COST.md).
- Lambda brings its own operational limits: cold starts, the account concurrency
  quota (10 on this new account), Little's law with slow dependencies. Those are real
  operational lessons and show up in the runbooks.
- Lambdas in a VPC need the NAT path for anything that isn't S3/DynamoDB (vendor,
  AppConfig, SSM). That dependency is visible and testable (scenario S5).

## When EKS is the right call

Many long-running services, steady load, a team already on Kubernetes, or workloads
that need sidecars/daemons. Then: EKS with managed node groups or Karpenter, one NAT
gateway per AZ, and the same golden-path idea as a Helm chart or an operator.
