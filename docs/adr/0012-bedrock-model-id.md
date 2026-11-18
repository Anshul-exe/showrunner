# 0012: Nova Lite by its in-region model ID, not the US inference profile

Date: 18/11/2026. Status: accepted.

## Context

Bedrock was blocked on the new account until 18/11/2026 (`ValidationException:
Operation not allowed`). After the user enabled it, the console playground answered
with the **US Nova Lite** cross-region inference profile, `us.amazon.nova-lite-v1:0`.
The agents (`ops/agents/*.md`) and their IAM policies use the in-region model ID
`amazon.nova-lite-v1:0`.

An inference profile routes requests to any region in its set (for the US profile:
us-east-1, us-east-2, us-west-2), so using it needs `bedrock:InvokeModel` on the profile
ARN **and** on the foundation model in every one of those regions, and the region
guardrails would have to allow those regions for Bedrock.

## Decision

Keep `amazon.nova-lite-v1:0`. Tested from the CLI with profile `showrunner` on
18/11/2026: both IDs answered (`Converse`, 5 max tokens each). The in-region ID needs no
policy change, keeps all traffic in us-east-1, and keeps the agents' IAM to one ARN per
agent.

## Consequences

- `sr preflight` checks the same ID and reports PASS.
- One triage run through the deployed Lambda on issue #1's evidence used the model
  (record: `docs/demo/model-triage.md`).
- If us-east-1 capacity throttles the demo, the profile is the fallback: change
  `model:` in the agent files and add the profile ARN plus the three regional model ARNs
  to their `iam:` blocks (CI checks the policies match the files).
