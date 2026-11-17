# network

One VPC per environment: two AZs, public subnets (NAT instance only), private subnets
(Lambda ENIs, Valkey), free gateway endpoints for S3 and DynamoDB, VPC flow logs
(3-day retention), and the NAT instance (`nat.tf`, ADR 0003).

**Why a VPC at all on a serverless core:** the cache needs one, and the job is about
networks. It also makes egress a visible dependency: everything that isn't S3 or
DynamoDB leaves through the NAT instance.

**Why one private route table:** there is one NAT instance. The instance writes its
own `0.0.0.0/0` route on boot (user data), so a replacement heals the route without
Terraform. Terraform doesn't declare the route, so there's no drift.

| Input | Meaning |
|---|---|
| `name` | e.g. `sr-dev` |
| `cidr` | VPC CIDR; public = `cidrsubnet(cidr, 8, i)`, private = `cidrsubnet(cidr, 8, 10+i)` |
| `azs` | two AZs |
| `permissions_boundary_arn` | `sr-workload-boundary` (every role the pipeline creates carries it) |

Outputs: VPC, subnet, route table and Lambda security group IDs, NAT ASG name.
