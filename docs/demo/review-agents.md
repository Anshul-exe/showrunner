# Demo: PR review agents (seeded PR)

What it shows: the `iac-security` specialist reports every IAM wildcard a Terraform PR
adds; the verifier **disproves** the false positives with code checks before any model
is asked, and only the confirmed finding counts. One PR comment, one check.

## The seeded change

`review/demo/seeded-pr.patch` adds three statements to the evidence Lambda's policy
(`platform/modules/ops-core/main.tf`):

| Statement | Looks like | Really |
|---|---|---|
| `s3:*` on `*` ("read flow logs") | a wildcard grant | **a wildcard grant**: any S3 action on any bucket |
| `states:SendTaskHeartbeat` on `*` | a wildcard grant | fine: the action has no IAM resource type, `*` is the only valid form (ADR 0008) |
| Deny `*` on `*` outside this account | the widest wildcard of all | fine: a Deny narrows access, it grants nothing |

checkov (run on the same tree) flags none of them: no scanner evidence, so the
specialist's findings are the only signal.

## Recorded run (18/11/2026, 11:27 UTC, local, same code and steps as the workflow)

Route, checkov, evidence, specialist, verifier and comment exactly as `review-agents.yml`
runs them; real Bedrock; only the daily counter stubbed (its table wasn't applied yet).
**1 model call**, 1,541 + 328 tokens, **$0.00017**. Instructions sha256 `a8e0abca340b`.

| Finding | Severity (model) | Verdict | Decided by |
|---|---|---|---|
| F1 `main.tf:228` `s3:*` on `*` | high | **confirmed** | statement check: Allow with wildcard actions and resources |
| F2 `main.tf:235` `states:SendTaskHeartbeat` on `*` | high | refuted | resource-type check |
| F3 `main.tf:243` Deny `*` | medium | refuted | statement check: Deny grants nothing |

The verifier model wasn't called: the checks settled all three. The gate fails (a
confirmed high). The comment it posts:

````markdown
<!-- sr-review -->
## Review agents

**1 confirmed**, 2 refuted by the verifier, 0 for a human. Specialist `iac-security`: model `amazon.nova-lite-v1:0` (1541+328 tokens, $0.00017); instructions sha256 `a8e0abca340b`. Verifier: checks only.

| Severity | Where | Finding | Evidence |
|---|---|---|---|
| **high** | `platform/modules/ops-core/main.tf:228` | IAM policy allows s3:* actions on all resources: The IAM policy added in the PR allows s3:* actions on all resources, which could potentially allow an attacker to read, write, or delete any S3 bucket and its contents. (E1) | Allow statement at lines 226-230 with wildcard actions ['s3:*'] and resources ['*'] |

<details><summary>2 finding(s) refuted by the verifier (not counted)</summary>

- ~~`platform/modules/ops-core/main.tf:235` IAM policy allows states:SendTaskHeartbeat actions on all resources (high)~~: states:SendTaskHeartbeat has no IAM resource type: `*` is the only valid resource (see docs/adr/0008-approver-role-scope.md)
- ~~`platform/modules/ops-core/main.tf:243` IAM policy denies all actions outside the account (medium)~~: lines 240-250 are a Deny statement: a wildcard there narrows access, it grants nothing

</details>

**Check result: failed** (a confirmed high). To override, add the label `accept-risk` and write the reason in a comment.

_Routed but not built yet (tier 3): `cost`._
````

## How it got here (the runs before this one)

| Time (UTC) | What happened | What changed |
|---|---|---|
| before 11:26 | first seed (s3:* + Deny): the model reported only `s3:*`; then s3:* + heartbeat + Deny: it reported `s3:*` and the heartbeat, the heartbeat refuted by the check (866 + 242 tokens) | - |
| 11:26 | same seed after a rebase onto the new table: the model's JSON had `"severy"` instead of `"severity"` in both findings; schema check failed, rules mode, 0 findings | structured output: the answer is now a forced tool call with the instruction file's schema (Converse `toolConfig`) |
| 11:27 | tool call valid first time, but only `s3:*` reported | prompt: the specialist is for recall; report every IAM statement with `*`, one per statement, and let the verifier settle it |
| 11:27 | the run above | - |

7 review model calls in total that day, about $0.001. Lesson worth saying in an
interview: a small model is a good *enumerator* and a poor *judge*; give judging to code
wherever a check exists, and to the model only what's left.

## Rehearsal on the demo branch (18/11/2026 20:43 UTC)

The seeded commit on a local branch `demo/review-seed`; every step of `review-agents.yml`
run locally on it, base = the `main` that will be pushed.

| Step | Result |
|---|---|
| route | `iac-security` (and `cost`, routed but not built) |
| evidence | 1 hunk; checkov: 0 failures on the platform |
| review, **without** the `sr-ops-agent-budget` table | counter not found -> no model call -> rules mode -> 0 findings -> the gate would **pass**. So the PR must be opened after the ops-core apply |
| review, with the counter (stubbed locally) | 1 model call, 1,541 + 339 tokens, $0.00017; F1 `s3:*` **confirmed**, F2 heartbeat and F3 Deny **refuted**, all by checks; exit 1 (confirmed high) |
| gate, no label | **fails**: "a confirmed high finding; to override, add the label `accept-risk`..." |
| gate, label but no reason comment | **fails**: "... there's no reason: <who> must write it in a PR comment" |
| gate, label + reason comment (simulated events) | **passes**; audit row built: pk `REVIEW#pr-<n>`, sk `<labelled at>#accept-risk`, by, at, PR, finding F1, reason link, run (not written: the first real write is the live demo) |

## Demo script (live PR)

Before the demo (the PR's runs take a few minutes): open the PR (commands in the
private run book). Then, in the demo:

1. **The PR and the comment** (30 s). "A routine-looking change: read flow logs for the
   case files. The review agent's comment: three findings, one confirmed, two refuted,
   with the reason for each. A small model is a good enumerator and a poor judge; code
   judges wherever a check exists." Point at the collapsed refuted list.
2. **The gate fails** (15 s). Checks tab: `review-agents gate` red on the confirmed high.
   It's a required check, so the PR can't merge. "It also fails closed: a scanner crash
   or a fork PR fails it, because GitHub counts a skipped required check as green."
3. **Accept the risk, on the record** (60 s). Write the reason as a PR comment, add the
   label `accept-risk`, then "Re-run failed jobs": only the gate re-runs (no model calls).
4. **The gate passes** and the comment gains an "Override" line with who, when and the
   reason link. Show the audit row:

   ```bash
   aws dynamodb query --table-name sr-ops-audit --key-condition-expression 'pk = :p' \
     --expression-attribute-values '{":p":{"S":"REVIEW#pr-<n>"}}' --output table
   ```
5. "The review role can write that row and nothing else: PutItem, this table, keys
   starting with REVIEW#, never an overwrite." (ADR 0013, simulator assertions.)
6. Close the PR without merging.

Fallback: this page (the recorded run and the rehearsal above).

If it differs: the model may word findings differently or skip one look-alike; the
verdicts come from the checks. "rules: ... (model not used: ...)" in the comment means
the model or the counter was unavailable: say so; the gate still enforces the rest.

## Real run on GitHub

Not run yet. (Recorded here with the date, the PR, the run times and the audit row once
the PR is open.)
