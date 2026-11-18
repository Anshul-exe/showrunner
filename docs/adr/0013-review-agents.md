# 0013: PR review agents: one specialist, a verifier that checks before it asks

Date: 18/11/2026. Status: accepted (tier 2 item 3).

## Context

The full design (Pillar 4) is a router, five specialist reviewers and a verifier that
tries to disprove each finding, posting one PR comment and a required check. Tier 2
builds one specialist plus the verifier. A reviewer that posts false positives gets
ignored, and a model asked "is this finding right?" tends to agree, so the verifier is
the part worth getting right.

## Decision

- **Router: rules, no AI.** Changed paths pick specialists (`review/review/core.py`).
  Routed-but-not-built specialists are named in the comment, not silently skipped.
- **Specialist: `iac-security`** (Terraform under `platform/`). Its evidence is the PR's
  added/changed hunks with new-file line numbers, plus checkov's failures *on those
  lines only*. Each finding must cite evidence IDs and point at one changed line;
  uncited ones are dropped in code. If the model is unavailable, checkov's failures
  on changed lines become the findings (rules mode), so the check still means something.
- **Verifier: code first, model second.** For each finding it runs checks that can
  *refute* it: is the line changed by this PR; for an IAM wildcard, read the actual
  statement around the line: a Deny statement, no wildcard, or an action with no IAM
  resource type (`*` is the only valid form, ADR 0008) refutes it; an Allow with a
  wildcard confirms it. Only findings the checks can't settle go to the verifier model,
  with the checks' results and the code around the line. Confirmed findings go in a
  table; refuted ones are listed collapsed with the reason, so the human sees what was
  filtered.
- **Gate:** a confirmed **high** fails the `review-agents gate` check. Override: label
  `accept-risk` plus a reason in a comment, then "Re-run failed jobs"; the gate reads
  the labels at run time, records who added the label (from the issue events API) in
  the comment, and passes. The re-run is the gate job only: no model calls.
- **Three jobs, three trust levels** (`review-agents.yml`):
  - `evidence` installs and runs checkov (third-party code) with `contents: read` only;
  - `review` holds the OIDC token for `showrunner-ci-review-agents` (Bedrock invoke on
    one model, and the call counter) and installs nothing: system `python3` (the
    package is standard library only) and the preinstalled AWS CLI;
  - `gate` has `pull-requests: write` and runs no repository code.
  Each job's harden-runner allow-list is only what that job calls.
- **Caps:** at most 2 model calls per run (`AWS_MAX_ATTEMPTS=1`, so no silent retries),
  and 40 per day through a conditional increment on `sr-ops-agent-budget` (DynamoDB,
  7-day TTL). If the counter can't be reached, no model call is made (fail closed:
  rules mode).
- **Contract:** instruction files `review/agents/*.md` (JSON front matter: model, token
  cap, scope, `iam`, output schema). `tests/review_agents` fails CI if the bootstrap role
  grants anything beyond the agents' `iam` plus the counter, or uses another model.

## Consequences

- Recorded run on the seeded change (18/11/2026 11:27 UTC, local, real Bedrock): one
  model call (1,541 + 328 tokens, $0.00017), three findings: `s3:*` on `*` confirmed by
  the statement check; `states:SendTaskHeartbeat` on `*` (no resource type) and a Deny
  `*` refuted. Verifier model not needed. `docs/demo/review-agents.md`.
- **Structured output via a forced tool call** (Converse `toolConfig`, the instruction
  file's schema as the tool's input schema): plain-text JSON from Nova Lite came back
  with a misspelt key (`"severy"`) on the same day. The schema is still validated.
- The specialist's prompt asks for recall (every IAM statement with `*`); precision is
  the verifier's job. With a precision-minded prompt the model silently skipped the
  look-alikes, so the comment couldn't show what was filtered.
- The review job runs the PR's own copy of `review/`, so a collaborator's PR could change
  the caps. The role limits the damage to Nova Lite calls (the Bedrock $5 budget alarm is
  the backstop); fork PRs get no OIDC token and are skipped.
- `accept-risk` is recorded in the PR (comment + GitHub's label event), not yet in the
  `sr-ops-audit` table: that needs a write permission on the review role (a bootstrap
  change, waiting for the owner's decision).

## When we'd decide differently

In production: run the review code from the default branch (a `workflow_run` on the PR's
checks, never `pull_request_target`), so a PR can't change its own reviewer; add the other
four specialists; feed more checkers to the verifier (OPA over the plan JSON is more robust
than reading HCL text); and track the false-positive rate per specialist from the
refuted list.
