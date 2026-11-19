# Roadmap

The core (tier 1) was built in two days on purpose: a working slice end to end, every
number from a real run. Each simplification below is a known upgrade, not an accident.
Rows refer to [the problem map](problem-map.md); P1, P2 and P17 are the three
six-month goals there, so each tier is also a plan for those goals.

## The core's simplifications

| Core today | Full design | Tier |
|---|---|---|
| one env, `dev` | dev + prod, prod behind a required reviewer (the `prod` GitHub environment already gates ops-core) | 3 |
| Lambda zip, alias moved at once (canary + provenance on the zip since 19/11/2026, ADR 0016) | container image, ECR by digest, build provenance; canary with alarm rollback | 2 |
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
   *Status 18/11/2026:* `iac-security` + verifier built (ADR 0013); recorded on the seeded
   change (1 confirmed, 2 refuted, 1 model call); first run on a real PR after the
   `sr-ops-agent-budget` table is applied.
4. **Network case files** (P17): S3 security-group drift and S4 DNS, with the network
   evidence tools added to the incident loop.
   *Status 19/11/2026:* built (ADR 0014). S3 drilled on dev twice (found and fixed a
   cache-outage-is-a-full-outage bug); S4 unit-tested, runs after the CI apply of the
   Resolver logs and flags schema.
5. **Safe deploys** (P7, P12): canary + scenario S2 (bad deploy rolled back by alarm);
   container image + provenance + deploy by digest.
   *Status 19/11/2026:* canary, provenance and deploy-by-digest built for the zip (ADR
   0016). CodeDeploy isn't on the AWS Free plan, so the deploy tool drives the Lambda
   alias weights itself (10% for 5 minutes, rolled back on the API's 5xx or latency
   alarm); the readiness gate requires a canary config. CI deploys after the owner
   applies the bootstrap change; until then `./sr deploy catalog-api` runs the same
   canary. Not built yet: the container image (after the round, owner 19/11/2026).

## Tier 3: the full design, ranked (not started; the owner picks after the round)

Estimates are for one person who knows this repo; "half a day" is ~4 hours including
tests, docs and a recorded run.

| # | Item | Problem | Estimate | Notes |
|---|---|---|---|---|
| 1 | **SLO burn-rate alarms**: an availability and a latency SLO per customer from API Gateway metrics (metric math, no new service), multi-window burn rate (1 h/5 min fast, 6 h/30 min slow) | P13 | half a day | also a traffic floor, so "OK on missing data" can't pass as healthy (issue #15) |
| 2 | **Cost report by customer**: Cost Explorer grouped by the `customer` tag, monthly per env, in the repo and on the issue tracker | P13, P15 | half a day | needs the tag activated as a cost-allocation tag (owner, console, ~24 h to show) |
| 3 | **Review verifier reads the plan JSON** instead of HCL text (ADR 0013 known limitation) | P6, P8 | half a day | `terraform show -json` from the plan job; references become values |
| 4 | **prod env** behind the required reviewer, promotion of the same attested zip | P7, P12 | 1 day | the `prod` environment already gates ops-core; a second NAT instance and cache: price it in COST.md first |
| 5 | **k6 breaking-point test** + event-readiness checklist (P10) | P10 | 1 day | capped (quota 400, 20 rps cap today); dev only |
| 6 | **park/unpark + handover note** | P15 | half a day | park = NAT ASG to 0 + cache left (minimum billed anyway) |
| 7 | **Scenario S5** (NAT instance loss) as a drill and a case file | P17 | half a day | the ASG and route self-heal exist; needs the evidence and the record |
| 8 | **MCP server** with read-only ops tools (status, evidence, audit rows) | P4 | 1 day | the same IAM-matches-instructions test |
| 9 | the other four **review specialists** | P6-P8 | 1 day | same router/verifier |
| 10 | an **eval set** of past incidents (#1, #2, #6, #15, S3, S4) before trusting the model's ranking | P2 | half a day | |
| 11 | Slack approvals, pager integration, on-call rota file | P2 | 1 day | |
| 12 | custom domain (after CloudFront is verified for the account), S6, New Relic export | - | half a day each | |

Tier 2 remainder, also after the round: the **container image** (arm64, ECR by digest,
provenance; 1 day).

## How this maps to the six-month goals

| Goal | Core (built) | Next |
|---|---|---|
| P1 self-sufficient teams | customer = one YAML file, plan comment per stack | readiness gate, customer added live by PR (tier 2) |
| P2 an AI-assisted operational process | the incident loop, measured: alarm -> issue in 3 s, proposal in ~11 s | model triage + eval set; review agents (tier 2) |
| P17 deployment, access, DNS, networking issues | VPC tier, NAT instance with a self-healing route | S3/S4 case files with real network evidence (tier 2), S5 (tier 3) |
