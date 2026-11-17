# 0003: NAT instance, not NAT gateway

Date: 17/11/2026 (recorded 18/11/2026). Status: accepted.

## Context

Lambdas in private subnets must reach the vendor, AppConfig and SSM on the internet.
A NAT gateway is ~$32.85/month per AZ idle, plus $0.045/GB processed. Two AZs: ~$66.

## Decision

One NAT **instance** per environment (`modules/network/nat.tf`):
- Amazon Linux 2023 arm64 on t4g.micro, AMI from AWS's public SSM parameter
- an Auto Scaling group of exactly 1 across both public subnets
- user data: IP forwarding, nftables masquerade, source/dest check off, then it
  points the private route table's `0.0.0.0/0` at itself: a replacement instance
  heals the route with no human and no Terraform run
- IMDSv2 only, no key pair, no inbound SSH; shell access is SSM Session Manager
- the route isn't in Terraform (Terraform ignores undeclared routes), so there's no
  drift between "who owns the route" and the state
- S3 and DynamoDB use free gateway endpoints and never touch the NAT

Cost: ~$10.42/month (instance $6.13 + public IPv4 $3.65 + disk $0.64), checked with the
Price List API on 18/11/2026.

## Consequences

- One instance is a single point of failure for internet egress. While the ASG
  replaces it (~2-3 minutes), vendor calls fail (catalog-api serves cached
  recommendations) and AppConfig polls fail (functions keep the last config).
  DynamoDB keeps working through its gateway endpoint. That's scenario S5.
- Bandwidth is the instance's (t4g.micro: up to 5 Gbit/s burst), plenty here.
- Patching is ours: a new AMI version changes the launch template and the ASG's
  instance refresh replaces the instance.

Observed at first apply (18/11/2026): the first launch failed with "Authentication
Failure" (the new instance profile hadn't propagated yet); the ASG retried and the
second launch succeeded and wrote the route by itself.

## At production scale

One NAT gateway per AZ, each AZ's private route table pointing at its own gateway.
The $66/month is noise next to production traffic, and it's managed.
