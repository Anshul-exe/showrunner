# 0005: Agents propose, humans approve

Date: 18/11/2026. Status: accepted.

## Context

An alarm at 3 a.m. needs the same first 15 minutes every time: open a record, pull
latency per dependency, check what changed, find the runbook, decide. That's slow and
error-prone for a tired human, and it's exactly what a model is good at summarising.
But a model that can change production is a model that can break it: it can be wrong,
and its input (alarm text, logs, issue comments) can be manipulated.

## Decision

The incident loop (`platform/modules/ops-core`, Step Functions `sr-ops-incident-loop`)
splits the work by who is allowed to do it:

| Step | Who | Can change anything? |
|---|---|---|
| open issue, evidence | code | no (read-only role; GitHub App writes the issue) |
| triage (hypotheses) | agent, `ops/agents/triage.md` | no: its role can only call its model |
| verify (confirm/refute) | code decides, agent explains | no: same |
| propose | code: highest-ranked confirmed hypothesis with a runbook `safe_action` that `applies_to` it | no |
| approve | **a human**: `/approve A1` by a CODEOWNER with admin/maintain | - |
| act | code, re-reading the action from the runbook | AppConfig of known customers only |
| check, post-incident review | code | no |

Rules that make this hold:
- **The menu is the runbook.** An agent can only rank hypotheses; the action comes
  from the alarm's runbook front matter, never from model output. `act` re-reads it
  from the bundled runbook rather than trusting the proposal in the state.
- **Deterministic verdicts.** The verifier's confirmed/refuted comes from numeric checks
  (ratio vs baseline, absolute delta, share of the API latency, Valkey server µs). The
  model may explain or settle `inconclusive`; it can't overturn a numeric verdict.
- **No model, still works.** If Bedrock is unavailable (as on this new account), triage
  falls back to a deterministic ranking and the loop still reaches the same proposal.
- **Permissions live in the instruction file.** Terraform builds each agent's IAM from
  its file's `iam` front matter (`agents.tf`); a test fails if it asks for more than
  model invocation. Agents never get deploy permissions.
- **Approval is out of band.** The task token sits in a hidden comment written by the
  GitHub App; `approve.yml` accepts only tokens from that App's comments, checks the
  commenter, and only then assumes a role that can do nothing but answer tokens
  (ADR 0008).
- **Everything is audited.** Each step writes a row (instruction file SHA-256, model,
  tokens, cost, evidence IDs, approver) to `sr-ops-audit`; the PIR is built from those
  rows, and Step Functions keeps the execution history for 90 days.
- **No answer, no action.** No approval in 30 minutes: escalate (SNS + label), change
  nothing.

## Consequences

- Mean time to a *proposal* is minutes; mean time to *action* still includes a human.
  That's the trade: a slower fix in exchange for no unreviewed production change.
- Only runtime-lane actions (AppConfig) are executable. Anything else (roll back code,
  scale the cache) is proposed as text for a human to do through the normal lanes.
- A new customer needs an ops-core apply before the loop can act for it (the act role
  lists their AppConfig apps by ID).

### The task token sits in a public issue comment (reviewed 19/11/2026)

The approval comment carries the Step Functions task token in an HTML comment
(`<!-- sr-task-token A1 ... -->`), so anyone can read it in this public repo. Why that
doesn't let anyone approve:

- **A token is not a credential.** Completing the task needs `states:SendTaskSuccess`
  with AWS credentials *in this account*. Outside it the token does nothing.
- **Who has that permission:** the approver role, which only `approve.yml` on `main`
  can assume (OIDC trust pinned to the workflow file and the branch, ADR 0008/0009);
  and the PowerUserAccess apply roles, pinned to `apply.yml` on `main` and their
  environment. The owner's admin user has it too. PR workflows (plan, review) get
  roles without it. Checked with the IAM simulator on 19/11/2026: `showrunner-ci-apply-dev`,
  `-apply-prod` and `-approver` are allowed; `-plan`, `-review-agents` and `-deploy-dev`
  are not. The action can't be scoped to one state machine: SendTaskSuccess
  has no resource type.
- **`approve.yml` decides whether the commenter may approve** (a CODEOWNER with
  maintain or admin), and it reads tokens only from comments made by the incident
  loop's GitHub App, so a pasted token is ignored.
- **The token is single-use and expires with the task** (30 minutes, then escalation).
  Inside the account it isn't secret anyway: execution history shows it to any role
  that can read Step Functions.
- **The act step re-checks** that the approved action is the one proposed, from the
  runbook's menu. It refuses anything else.

What it doesn't stop: a principal that already has those AWS permissions could approve
under any name, with or without the comment. That's an account-access problem, not a
GitHub one. **Planned (ROADMAP):** keep the token out of GitHub. Write it to the audit
table (`pk` = incident, `sk` = `TOKEN#A1`). The App's comment then carries only a
reference, and the approver role gets `dynamodb:GetItem` on those keys alone. Not done
before the round: it changes the `/approve` path the demo relies on, and needs a
bootstrap apply plus an incident deploy.

## When we'd decide differently

With a track record (say, 20 incidents where the approved action was always the
proposed one), low-blast-radius actions like a vendor fallback flag could auto-execute
with a human notified instead of asked, still within the runbook menu.
