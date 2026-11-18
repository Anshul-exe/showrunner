# review/: PR review agents

Runs on every same-repo PR (`.github/workflows/review-agents.yml`). Tier 2 builds one
specialist (`iac-security`) and the verifier; the router already names the other four
(tier 3) when a PR would need them. Decision record: `docs/adr/0013-review-agents.md`.
Demo: `docs/demo/review-agents.md`.

```
changed paths --route (rules)--> iac-security --findings--> verifier --> one PR comment + gate
                                  ^ evidence: changed hunks       | code checks first;
                                  | + checkov hits on those lines | the model only for what
                                                                  v they leave open
```

| File | What |
|---|---|
| `agents/iac-security.md`, `agents/review-verifier.md` | instruction files: JSON front matter (model, token cap, scope, `iam`, output schema), then the system prompt |
| `review/core.py` | router, evidence, specialist (or the rules fallback), verifier checks |
| `review/iam.py` | reads the IAM statement around a Terraform line (HCL `statement {}` and `jsonencode`) |
| `review/agents.py` | loads instruction files, calls Bedrock (Converse, forced tool call = structured output), the caps |
| `review/render.py` | the comment: confirmed table, refuted collapsed with the reason |
| `demo/seeded-pr.patch` | the seeded change for the demo |

Why it's built this way:
- **Standard library only.** The job holding the AWS token installs nothing.
- **The verifier checks before it asks.** Deterministic checks can only refute or confirm
  with a reason a human can read; the model sees only the findings no check applies to.
- **Caps:** 2 model calls per run, 40 per day (`sr-ops-agent-budget`), no retries; no
  counter, no call.

Run locally (real Bedrock, profile `showrunner`):

```bash
git diff -U3 origin/main...HEAD > /tmp/d.txt; git diff --name-only origin/main...HEAD > /tmp/p.txt
PYTHONPATH=review python3 -m review route < /tmp/p.txt | jq -r '."iac-security"[]' > /tmp/f.txt
PYTHONPATH=review python3 -m review evidence --diff /tmp/d.txt --files /tmp/f.txt > /tmp/e.json
PYTHONPATH=review python3 -m review run --evidence /tmp/e.json | PYTHONPATH=review python3 -m review comment
```
