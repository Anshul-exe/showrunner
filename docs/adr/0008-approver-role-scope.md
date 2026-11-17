# 0008: The approver role can't be scoped to one state machine

Date: 18/11/2026. Status: accepted.

## Context

The incident loop (day 2) waits on a Step Functions task token; a CODEOWNER's
`/approve A1` comment makes `approve.yml` call `SendTaskSuccess`. The obvious least
privilege would be "SendTaskSuccess on the incident state machine only", but
`states:SendTaskSuccess` and `states:SendTaskFailure` have no resource type in the
Service Authorization Reference, so the policy needs `Resource: "*"`.

## Decision

The scope comes from elsewhere:
- the task token is unguessable and single-use, and only the incident issue holds it
- the role (`showrunner-ci-approver`) trusts only `approve.yml` on `refs/heads/main` in
  this repository (OIDC `job_workflow_ref` + `sub`), so a PR can't borrow it
- `approve.yml` checks the commenter's permission before calling the API
- the role can do nothing else (the IAM guardrail test asserts it can't
  `StartExecution`)

## Consequences

Anyone who can run `approve.yml` on main with a valid token can answer that token.
That's the intended control: approval is a CODEOWNER action recorded in the issue and
the audit table.
