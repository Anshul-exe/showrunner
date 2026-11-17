# cache

One ElastiCache Serverless for Valkey cache per environment, shared by the env's
customers and separated by per-customer IAM-authenticated ACL users (ADR 0006).

- Valkey 8, TLS always on, usage limits 1 GB / 1,000 ECPU/s (cost cap), no snapshots.
- Security group: 6379-6380 from the env's Lambda security group only.
- User group (engine VALKEY, so no `default` user): `sr-<env>-ops` (`+ping` only) plus
  each customer's user, which the customer stacks add (`ignore_changes = [user_ids]`
  here, so the two states don't fight).

**Why serverless:** no node sizing, and a 100 MB minimum (~$6/month) instead of a
cache.t4g.micro node plus its replica. **Why IAM auth:** no password to store, rotate,
or decrypt during a CI plan.
