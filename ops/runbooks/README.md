# Runbooks

One runbook per alarm. Every alarm's description links here (`Runbook: .../<name>.md`).

The YAML front matter is machine-readable. `safe_actions` is the **complete** list of
actions the incident loop (day 2) may propose for this alarm. The triage agent can't
propose anything else, and every action still needs a human `/approve`.

| Field | Meaning |
|---|---|
| `alarm` | alarm name suffix (`sr-<customer>-<env>-<alarm>`) |
| `severity` | sev1 pages immediately; sev2/sev3 notify |
| `safe_actions[].lane` | `runtime` = an AppConfig deployment (no redeploy); `change` = a PR through CI |
| `safe_actions[].applies_to` | the component the action fixes (`vendor-recs`, `valkey`, `dynamodb`, `config-change`). The incident loop proposes an action only when the verifier **confirmed** a hypothesis about that component. |
| `safe_actions[].appconfig` | profile and the keys to set |
| `safe_actions[].blast_radius` | what else the action affects |
| `safe_actions[].rollback` | how to undo it |

| Runbook | Alarm |
|---|---|
| [vendor-latency](vendor-latency.md) | vendor recommendations p95 |
| [dependency-errors](dependency-errors.md) | errors from Valkey, DynamoDB or the vendor |
| [api-latency](api-latency.md) | API p95 (what viewers feel) |

## Who gets told what

| | dev | prod |
|---|---|---|
| alarm enters ALARM | email (SNS) + the incident issue | email (SNS) + the incident issue |
| alarm returns to OK | the incident issue only (recovery check, post-incident review) | email too: on-call needs to know it cleared, maybe before the loop's check |
| incident loop | starts on ALARM via EventBridge (independent of the email actions) | same |

Why no OK email in dev (since 18/11/2026): one S1 run sent an email per alarm per
transition, which teaches people to ignore the inbox. The issue already says when the
service recovered. GitHub's own emails for the issue follow each person's notification
settings (watch the repo with "Issues" off, or filter on the `incident` label).
