---
{
  "name": "review-gate",
  "kind": "step",
  "purpose": "Pass or fail the PR's review check, fail closed, and record accept-risk overrides.",
  "model": null,
  "reads": ["the evidence and review jobs' results", "the PR's labels, label events and comments",
            "the confirmed high findings"],
  "never_touches": ["any model", "any audit row that isn't REVIEW#...", "an existing audit row (no overwrite)"],
  "writes": ["one sr-ops-audit row per accept-risk label event: pk REVIEW#pr-<n>, sk <labelled at>#accept-risk"],
  "iam": [{"actions": ["dynamodb:PutItem"],
           "resources": ["table/sr-ops-audit"],
           "conditions": [{"test": "ForAllValues:StringLike", "variable": "dynamodb:LeadingKeys", "values": ["REVIEW#*"]},
                          {"test": "Null", "variable": "dynamodb:LeadingKeys", "values": ["false"]}]}]
}
---
Not an AI agent: a deterministic step. Its behaviour is `review/review/gate.py`, and the
rules, in order, are:

1. Fork PR: fail, "not reviewed automatically: fork PR". Fork PRs get no AWS token, so
   no review can run; a maintainer reviews by hand.
2. The evidence job didn't succeed (scanner crash, cancelled): fail closed.
3. No Terraform changed: pass (nothing for iac-security).
4. The review job didn't succeed: fail closed.
5. No confirmed high finding: pass.
6. A confirmed high and no `accept-risk` label: fail.
7. `accept-risk` set but no reason comment by whoever added it (at most an hour before
   the label, or after it): fail.
8. Otherwise pass, and write the override to `sr-ops-audit`: who added the label, when,
   the PR, the confirmed high findings, the reason comment's link and the run. PutItem
   with `attribute_not_exists(pk)`: a re-run finds its row and doesn't overwrite it.

The job always runs, because GitHub treats a skipped required check as passing.
