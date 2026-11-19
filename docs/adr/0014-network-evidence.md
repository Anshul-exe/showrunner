# 0014: Network evidence in the incident loop; Reachability Analyzer capped

Date: 19/11/2026. Status: accepted (tier 2 item 4: case files S3 and S4).

## Context

The core loop explains dependency problems (S1: a slow vendor) from metrics. Networking,
access and DNS problems look the same from a metric's point of view: the cache "fails"
whether Valkey is down or a security-group rule was removed. The design (Pillar 2b)
asks for network evidence: who changed what, what the network actually rejects, which
names don't resolve, and a path check.

## Decision

- **Five tools, one library** (`ops/lib/opslib/network.py`), used by the evidence step:
  CloudTrail security-group/route/NACL changes with who and when; flow-log REJECTs to
  the cache ports (Logs Insights, the last 10 minutes vs the hour before); Route 53
  Resolver NXDOMAIN/SERVFAIL by name; the cache's security group compared with the one
  rule Terraform declares; Reachability Analyzer from a catalog-api network interface
  to a Valkey endpoint on 6379.
- **The live rule check is the primary signal; CloudTrail explains it.** CloudTrail
  delivers events 5-15 minutes late, so "no change found" right after an incident
  means nothing. The security group's current rules are read live.
- **Reachability Analyzer only on a network signal** (drift, or a surge of REJECTs),
  at most **2 runs a day** (`reachability#<day>` in `sr-ops-agent-budget`, a conditional
  increment), and the path and analysis are deleted afterwards. No counter, no run.
  $0.10 an analysis: the cap bounds it at $0.20 a day ($6 a month worst case; one run per S3 drill in practice). Measured: 21 s per analysis
  (18/11/2026), inside the evidence Lambda's 90 s.
- **Resolver query logs** for the VPC to CloudWatch Logs, 3-day retention, like flow
  logs. Query logging is free; ingestion is $0.50/GB.
- **Root cause before workaround.** A confirmed `network` hypothesis, or a config change
  that points a failing dependency elsewhere (a `vendor_url` flag), is acted on before
  a workaround such as `vendor-fallback-on`.
- **Two new runbook actions, two lanes.** `rollback-last-appconfig-deployment` (runtime)
  is now executable: it redeploys the version before the deployment the analysis cited,
  and refuses if anything was deployed since. `reapply-shared-stack` (change lane) is
  shown as a recommendation with the command; the loop never runs a pipeline.
- **`vendor_url` flag** (S4's lever) is optional and limited by the schema to https
  Lambda function URLs (`*.on.aws`), so a config change can't point the service at an
  arbitrary host.
- **Valkey connections are recycled after 120 s**, so a security-group change (and a
  fresh IAM token) takes effect within two minutes instead of at the next cold start.

## Permissions (evidence role, ops-core)

Read-only network calls on `*`: 23 exact action names, each checked against the Service
Authorization Reference on 19/11/2026 (none has a resource type, so `*` is the only valid
resource). Two reads that *do* have a resource type are scoped instead:
`ec2:GetManagedPrefixListEntries` to prefix lists (this account's and the AWS-managed
ones), and `logs:GetQueryResults` with `logs:StartQuery` only on `/sr/sr-<env>/*`.
`elasticache:DescribeServerlessCaches`,
Reachability Analyzer create/start, `ec2:CreateTags` only while creating its own path or
analysis (`ec2:CreateAction` condition), delete **only for paths tagged `sr=reachability`**, and `dynamodb:UpdateItem` on the budget table only for
`reachability#*` keys. The analyzer reads the path's components with the caller's
permissions, so the read list follows AWS's `AmazonVPCReachabilityAnalyzerFullAccessPolicy`
(v5) minus Transit Gateway, VPN and Direct Connect, which this platform doesn't have.

### Why the review agents don't flag the `*` statement

The review verifier (ADR 0013) refutes a wildcard finding only when **every** action in
the statement is on `review/review/iam.py` `NO_RESOURCE_TYPE`. Before this change the list
knew 2 of these 23 actions, so the PR would have failed its own gate on a false positive.
The fix was to extend the list, under three rules (the user's decision, 19/11/2026):

- **Exact names only, never patterns.** `ec2:Describe*` would also cover actions AWS adds
  later, unchecked. A pattern (`ec2:Get*`, `s3:*`) on `*` is always confirmed; a test
  pins that.
- **Each name checked against the reference**, with the source page in a comment.
  Machine-readable copy used for the check:
  `https://servicereference.us-east-1.amazonaws.com/` (`ec2`, `tiros`, `logs`).
- **An action with a resource type never goes on the list**, even a read: it gets its own
  scoped statement. A statement mixing one of those with no-resource-type actions on `*`
  stays a finding (tested).

Alternative rejected: `accept-risk` on the case-files PR. It would have worked, but a
reviewer that flags every Describe-on-`*` teaches people to override it.

## Consequences

- S3 is diagnosed without a model: three independent checks confirm `network`.
- S4 depends on the Resolver logs being delivered (a few minutes); before that the
  config-change hypothesis is inconclusive, and the model may weigh in, but checks
  alone don't propose the rollback.
- Not built: IAM policy simulation (access problems), drift for every resource (only
  the cache rule is compared), route-table drift, S5/S6.
- In production: separate accounts per environment (ADR 0002) and AWS Config rules for
  drift, instead of one hand-written comparison.
