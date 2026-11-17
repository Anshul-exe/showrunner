# Cost

The account is on the AWS **Free plan**: charges are paid from credits, and when the
credits run out the account closes instead of billing. So cost is a hard constraint,
and every service gets a line here before it's created.

Guardrails (bootstrap, applied 18/11/2026): a $60/month budget (50/80/100% actual,
100% forecast), a $5/month Bedrock budget, both on **gross** cost (credits excluded,
or they'd never alert), and Cost Anomaly Detection once Cost Explorer is enabled.

## Prices used (us-east-1)

Checked on 18/11/2026 with the AWS Price List API (`aws pricing get-products`),
except where marked.

| Item | Price |
|---|---|
| EC2 t4g.micro (NAT instance) | $0.0084/hour |
| Public IPv4 address (in use) | $0.005/hour |
| EBS gp3 | $0.08/GB-month (baseline 3,000 IOPS / 125 MB/s included) |
| ElastiCache Serverless, Valkey data storage | $0.084/GB-hour, **100 MB minimum** |
| ElastiCache Serverless, Valkey ECPUs | $0.0023 per million |
| CloudWatch custom metrics | $0.30/metric-month (first 10,000) |
| CloudWatch alarms (standard resolution) | $0.10/alarm metric-month |
| CloudWatch Logs ingestion / storage | $0.50/GB / $0.03/GB-month |
| CloudWatch Logs data protection | $0.12/GB scanned |
| CloudWatch dashboards | $3/dashboard-month after the first 3 (pricing page, not the API query) |

## The core, running all month (tier 1)

| Item | Per month | Notes |
|---|---|---|
| NAT instance t4g.micro, 730 h | $6.13 | ADR 0003: a NAT gateway would be ~$32.85 + $0.045/GB |
| Its public IPv4 | $3.65 | |
| Its 8 GB gp3 root volume | $0.64 | |
| Valkey Serverless, 100 MB minimum | $6.13 | one cache for the env, shared by customers (ADR 0006) |
| Valkey ECPUs | < $0.05 | demo traffic is a few requests/s for minutes at a time |
| CloudWatch: 12 custom metrics (2 customers x 3 dependencies x 2) | $0.60-3.60 | $0.60 if the always-free 10 metrics apply to this plan |
| CloudWatch: 6 alarms = 10 alarm metrics | $0-1.00 | the dependency-errors alarm sums 3 metrics |
| CloudWatch: 1 dashboard | $0 | |
| Logs (Lambda, API access, VPC flow logs, 3-7 day retention) | < $0.50 | idle most of the time |
| Lambda, API Gateway, DynamoDB on-demand, AppConfig, SNS, SSM | < $1 | request-priced, near zero at idle |
| CloudTrail (one trail, management events) + its S3 bucket | < $0.10 | first copy of management events is free |
| **Total** | **~$17-22** | target in the brief: $22-28 |

Each new customer adds ~$0.60-2 (its 6 metrics, 5 alarm metrics, a table, a function):
the expensive parts (NAT, cache) are per environment, not per customer. That's the
pool-vs-silo trade-off in ADR 0006, in numbers.

**Not yet measured:** the first real bill. Cost Explorer data lags ~24 h; this table
gets an "actual" column after the first full week.

## Never created (the conftest policy and the CI permission boundary both enforce it)

EKS (~$73/month control plane), NAT gateways, RDS/Aurora, MQ, OpenSearch, ALB/NLB,
interface VPC endpoints ($7+/month each per AZ), customer-managed KMS keys ($1 each),
Secrets Manager ($0.40/secret), any EC2 instance but the NAT instances.

## Lambda concurrency (not money, but a limit)

The account's concurrency quota is **10** (checked 18/11/2026; increase to 1,000
requested). A slow dependency multiplies concurrency (Little's law: concurrency =
requests/s x seconds per request), so `sr demo traffic` caps itself at 4 requests/s
while the quota is 10, no reserved concurrency is set, and every vendor call has a
2.5 s timeout.
