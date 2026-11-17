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

## When we'd decide differently

With a track record (say, 20 incidents where the approved action was always the
proposed one), low-blast-radius actions like a vendor fallback flag could auto-execute
with a human notified instead of asked, still within the runbook menu.
