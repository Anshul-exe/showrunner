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
