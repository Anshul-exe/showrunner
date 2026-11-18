# Roadmap

The core (tier 1) was built in two days on purpose: a working slice end to end, every
number from a real run. Each simplification below is a known upgrade, not an accident.
Rows refer to [the problem map](problem-map.md); P1, P2 and P17 are the three
six-month goals there, so each tier is also a plan for those goals.

## The core's simplifications

| Core today | Full design | Tier |
|---|---|---|
| one env, `dev` | dev + prod, prod behind a required reviewer (the `prod` GitHub environment already gates ops-core) | 3 |
| Lambda zip, alias moved at once | container image, ECR by digest, build provenance; CodeDeploy canary with alarm rollback | 2 |
| API Gateway URL | CloudFront + S3 player page per customer | 2 |
| incident verifier only | PR review agents: router -> specialist -> verifier | 2 |
| scenario S1 only | case files S3 (security-group drift), S4 (DNS) with flow logs, Resolver query logs, Reachability Analyzer | 2 |
| local traffic script, 4 rps cap (Lambda quota 10) | viewer simulation + k6 breaking-point test, event mode | 3 |
| budgets + a written cost model | cost report by customer tag, park/unpark, handover note, MCP server | 3 |
| model triage (Nova Lite, since 18/11/2026), deterministic rules as the fallback | an eval set of past incidents before trusting its ranking | 2 |
| SNS email escalation | pager integration, on-call rota file, Slack approvals | 3 |

## Tier 2, in this order (about half a day each)

1. **Customer page** (P16): S3 + CloudFront (OAC) per customer, `/api` behaviour, a
   tiny catalog + player page. Makes the "streaming customer" visible.
2. **Self-service polish** (P1): readiness gate on the customer-YAML PR (conftest:
   every alarm has a runbook, every dependency has alarms); a third customer added live
   by PR.
3. **Review agents** (P6, P7, P8): one specialist (`supply-chain` or `iac-security`)
   plus the verifier on PRs; a seeded PR with one confirmed and one refuted finding.
4. **Network case files** (P17): S3 security-group drift and S4 DNS, with the network
   evidence tools added to the incident loop.
5. **Safe deploys** (P7, P12): CodeDeploy canary + scenario S2 (bad deploy rolled back
   by alarm); container image + provenance + deploy by digest.

## Tier 3: the full design

prod env; synthetic SLO + burn-rate alarms; event readiness + k6 breaking-point test
(P10); cost report + park/unpark + handover (P13, P15); MCP server with read-only ops
tools (P4); scenarios S5 (NAT instance loss) and S6; custom domain; Slack approvals;
New Relic export.

## How this maps to the six-month goals

| Goal | Core (built) | Next |
|---|---|---|
| P1 self-sufficient teams | customer = one YAML file, plan comment per stack | readiness gate, customer added live by PR (tier 2) |
| P2 an AI-assisted operational process | the incident loop, measured: alarm -> issue in 3 s, proposal in ~11 s | model triage + eval set; review agents (tier 2) |
| P17 deployment, access, DNS, networking issues | VPC tier, NAT instance with a self-healing route | S3/S4 case files with real network evidence (tier 2), S5 (tier 3) |
