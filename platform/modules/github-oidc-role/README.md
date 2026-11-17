# github-oidc-role

One IAM role that a GitHub Actions job can assume through OIDC. No access keys exist anywhere.

**Trust conditions (all must match):**

| Claim | Why |
|---|---|
| `aud = sts.amazonaws.com` | the token was minted for AWS |
| `repository_id = <number>` | the immutable repo ID, so a renamed or re-created repo with the same name can't assume it |
| `sub` | ties the token to an event type: `pull_request`, `environment:<env>`, or `ref:refs/heads/main` |
| `job_workflow_ref` | only the named workflow file (and ref) can use the role. A new workflow added in a PR can't borrow the apply role. |
| `environment` (optional) | the job must run in a GitHub environment, which is where required reviewers live |

AWS supports GitHub's custom claims (`job_workflow_ref`, `repository_id`, `environment`) as STS
condition keys directly, so the repo's `sub` format doesn't need customising.

Every role gets a **permission boundary**, so even a mistaken inline policy can't exceed it.

**Why a module:** bootstrap creates the CI roles with it, and phase 1's `streaming-service`
module reuses it for per-environment deploy roles, so every OIDC trust in the repo has the
same shape.
