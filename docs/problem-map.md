# Problem map

Showrunner answers problems that Accedo, a company that builds and operates streaming
services for many customers, has described **in public**: a job post and articles on
its own site. This page quotes only those public sources (links below, all opened
17/11/2026). It claims no knowledge of anyone's internal systems; the code is generic
and its customers are fictional.

Status: **built** = in the 2-day core and run on `dev`; **tier 2 / tier 3** = on the
[roadmap](ROADMAP.md), not built.

| # | What was said (source, date) | What Showrunner does | Status |
|---|---|---|---|
| P1 | Six-month goal: "Improve a shared infrastructure or deployment capability that makes engineering teams more self-sufficient" ([job post][job], 09/11/2026) | A new customer environment is one YAML file in a PR -> plan comment -> merge -> applied | built (plan comments); readiness gate: tier 2 |
| P2 | Six-month goal: "Improve at least one recurring operational process through automation, an AI-assisted workflow or better documentation" ([job post][job]) | The incident loop: alert -> evidence -> triage -> ticket -> approved fix, with timings | built, approval step not yet run end to end |
| P3 | "safe, auditable AI-assisted workflows … scoped access, appropriate human approval and clear evidence" ([job post][job]) | Each agent has an instruction file that is also its IAM policy; every claim cites evidence IDs; approval happens in the ticket; audit table | built |
| P4 | The job post also names MCP integrations, alert enrichment, incident triage, telemetry investigation, automated reporting ([job post][job]) | Alert enrichment + triage in the incident loop; MCP server and reports | loop built; MCP + reports: tier 3 |
| P5 | "Every AI-generated insight requires a human to translate it into a ticket, build it, test it, and deploy it" … the loop is "weeks or months rather than minutes or hours"; capabilities should change "without a redeployment" ([F. Andersson][act], 23/07/2026) | Insight -> ticket in seconds; the fix is a runtime config change (AppConfig), no redeploy; alarm -> ticket and alarm -> mitigation are measured | built |
| P6 | "a hierarchy of specialized review agents rather than a single general-purpose assistant"; agent permissions in "instruction files"; "a separate agent tries to disprove" each finding; "Shift Left assurance" ([R. Arora][gov], 10/08/2026) | Instruction files with permissions (built); the incident verifier refutes hypotheses with numbers (built); PR review agents with a verifier | tier 2 |
| P7 | Risk: "a compromised dependency that leaks CI/CD secrets"; "a forged commit disguised as routine maintenance" ([R. Arora][gov]) | OIDC only (no keys), SHA-pinned actions, hash-locked dependencies, egress blocked by harden-runner, prod-role stacks behind a reviewer; a harmless "evil dependency" demo; build provenance | built; demo PR + provenance: next |
| P8 | A shared logging library logged auth headers, cookies and personal data into widely readable diagnostic storage ([R. Arora][gov]) | Allow-list redacting logger + a CI test that fails on leaked headers | built |
| P9 | Endpoint-level metrics for "every dependency the service interacts with" are "a condition of readiness"; dashboards "incorrectly attributed latency spikes to a Redis cache"; "Redis operates in microseconds, and the delays we were seeing were measured in seconds"; the cause was "an external vendor integration that wasn't visible at all in the original dashboards" ([D. Bell][scale], 14/04/2026) | A real Valkey cache in the request path, per-dependency latency and errors, a mock vendor; the verifier refutes "the cache is slow" with Valkey's own microsecond latency (scenario S1) | built |
| P10 | Load tests should find "the breaking point, not confirming the system holds"; "a test that always passes isn't protecting anyone" ([D. Bell][scale]) | Breaking-point load test with a browse/search mix | tier 3 |
| P11 | "defined escalation paths", "single Incident Record", "ITIL Problem Management … root-cause analysis", "risk-based change enablement", "post-change monitoring" ([service & partner management][spm], current site) | One GitHub issue per incident with alarms correlated into it, escalation on no approval, post-change check of user-facing p95, PIR from the audit trail, problem-management label on repeats; AppConfig strategy with alarm rollback | built |
| P12 | "on-call rotations, incident response, and post-incident reviews", "redundancy, failover, and recovery" ([Global Ops & Engineering ad, Delhi][delhi26], 2026, since removed) | Vendor fallback flag, self-healing NAT route, PIR; canary with auto-rollback | built; canary: tier 2 |
| P13 | "service uptime levels for both external services for clients and internal high impacting tools", "cost optimisation" ([Infra Ops lead ad, Delhi][delhi25], 2025, since removed) | Per-customer cost tags + budgets; synthetic uptime checks, cost report, park/unpark | tags/budgets built; rest: tier 3 |
| P14 | "Budgets are more constrained", "Who owns the decisions that AI informs?" ([L. Gaydon][ibc], 21/09/2026) | The audit record names the human who approved each AI-proposed action; cost guardrails and a written cost model | built |
| P15 | Client platforms: "15–25% of cloud spend wasted", "35–45% [engineering capacity] consumed by maintenance" ([02/04/2026][waste]) | Cost decisions written down with prices (NAT instance vs gateway); cost report by customer tag | model built; report: tier 3 |
| P16 | Running many customers' services, migrating then operating them ([RecordPlus][recordplus] 09/2026; [Exxen][exxen] 06/2025) | Two customers from one module, isolated by state, IAM, tags and per-customer cache users with key-prefix ACLs | built |
| P17 | Six-month goal: "safely diagnose and resolve common deployment, access, DNS, networking and service issues"; "core networking concepts such as VPCs, subnets, routing, DNS, HTTP and TLS"; desirable: "ElastiCache/Valkey" ([job post][job]) | A VPC tier (private subnets, routing, NAT instance, gateway endpoints, TLS to Valkey, flow logs); network case files S3/S4 with flow logs, DNS query logs, Reachability Analyzer | VPC built; case files: tier 2 |

[job]: https://www.linkedin.com/jobs/view/4487918148
[gov]: https://www.accedo.tv/insights-and-news/ai-agents-are-already-inside-your-platform-has-your-governance-caught-up
[act]: https://www.accedo.tv/insights-and-news/can-your-platform-actually-act-on-ai-most-cant
[scale]: https://www.accedo.tv/insights-and-news/how-operating-streaming-platforms-at-scale-changed-the-way-we-build-them
[spm]: https://www.accedo.tv/expertise/service-and-partner-management
[ibc]: https://www.accedo.tv/insights-and-news/what-ibc-2026-was-really-about-it-wasnt-ai
[waste]: https://www.accedo.tv/insights-and-news/how-decision-paralysis-and-inaction-is-draining-streaming-operations
[delhi26]: https://builtin.com/job/senior-full-stack-engineer/8971788
[delhi25]: https://builtin.com/job/senior-manager-infrastructure-director-operations/6424776
[recordplus]: https://www.accedo.tv/insights-and-news/accedo-powers-modernization-of-recordplus-ott-service-4v1s7
[exxen]: https://www.accedo.tv/insights-and-news/accedo-manages-the-exxen-video-service-for-acunmedya

Left out on purpose: an older (2021) live-event press release and an undated case study
(stale); AWS Fault Injection Service (not on the Free plan, so chaos is an AppConfig
flag); EKS and NAT gateways (cost: ADR 0001, 0003).
