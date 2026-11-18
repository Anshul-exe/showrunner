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

## Run it live

After `sr-ops-agent-budget` exists (ops-core apply):

```bash
git switch -c demo/review-seed origin/main
git apply review/demo/seeded-pr.patch
git commit -am "feat(ops): network logs for case files (seeded review demo, do not merge)"
git push -u origin demo/review-seed
gh pr create --title "Seeded review demo (do not merge)" --body "Seeded PR for the review-agents demo."
# watch: review-agents gate fails with 1 confirmed, 2 refuted
# override path: gh pr edit --add-label accept-risk; comment the reason; re-run failed jobs
gh pr close demo/review-seed --delete-branch    # afterwards
```

The model may word findings differently, or skip one, on a live run; the verdicts come
from the checks, so a reported finding gets the same verdict. Fallback: this page.
