# 0011: The plan role may decrypt only via Lambda

Date: 18/11/2026. Status: accepted (the user's decision on 18/11/2026).

## Context

PR plans run as `showrunner-ci-plan`: ReadOnlyAccess, plus denies, because a plan
executes code from the PR branch (providers, data sources). It had a blanket
`kms:Decrypt` deny. Lambda encrypts function environment variables with the
`aws/lambda` key and decrypts them with the caller's permissions when returning the
function's configuration, so the plan role saw every function's environment as empty.
Every PR plan showed `~ environment { + variables = {...} }` on every function: a false
diff that trains reviewers to skim the plan.

## Decision

`DenyDecryptExceptLambdaEnv`: deny `kms:Decrypt` unless `kms:ViaService` is
`lambda.us-east-1.amazonaws.com`. `secretsmanager:GetSecretValue` keeps its own
unconditional deny. Unchanged: SecureString parameters are decrypted via SSM, so they
stay denied (and `/showrunner/secret/*` reads are denied outright).

What this exposes to PR code: the environment variables of the account's functions.
They hold names, ARNs and endpoints only; secrets live in SSM SecureString and are read
at runtime (CLAUDE.md rule 2), and the module tests keep it that way.

## Verified

- `iam-guardrails.sh`: decrypt via SSM or direct is `explicitDeny`; via Lambda it's no
  longer denied by us (the grant comes from the `aws/lambda` key policy, which the
  simulator doesn't evaluate, so it reports `implicitDeny`)
- the first PR plan after the change ([PR #5](https://github.com/Anshul-exe/showrunner/pull/5),
  18/11/2026): acme-tv, nordic-sports and ops-core plan as "no changes"; on PR #3 every
  function in them had shown `+ variables`

## When we'd decide differently

If a function ever needs a secret in its environment, that's the bug to fix (SSM at
runtime); if it can't be, restore the blanket deny and accept the false diff.
