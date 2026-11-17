# Agent instruction files

Each AI step in the incident loop has one file here. The YAML front matter is the
agent's contract, and it's enforced, not just documented:

| Key | Enforced by |
|---|---|
| `iam` | Terraform builds the agent's IAM role policy **from this list** (`modules/ops-core`), so the file and the policy can't drift. `tests/ops/test_agents.py` fails CI if an agent is given anything but model invocation. |
| `model`, `max_tokens`, `temperature` | the Lambda reads them from this file at runtime |
| `may_propose` | the Lambda drops any output outside it (e.g. an action not in the alarm's runbook) |
| `output_schema` | the Lambda validates the model's JSON against it; invalid output = the step falls back to deterministic analysis |
| `reads`, `never_touches` | documentation of scope, matched by the IAM list (an agent with only `bedrock:InvokeModel` can't touch anything else) |

The body is the system prompt. The file's SHA-256 is written to the audit table with
every call, so each decision can be traced to the exact instructions in force.

Agents propose; humans approve (ADR 0005). No agent has a write permission: the only
step that changes anything is `act`, which isn't an AI agent, runs only after a human's
`/approve`, and can only execute a runbook's `safe_actions`.
