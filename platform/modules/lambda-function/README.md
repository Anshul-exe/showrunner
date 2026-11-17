# lambda-function

The golden path for a function: everything a platform team wants on every Lambda.

- Python 3.12 on arm64 (cheaper per GB-second than x86).
- A role named after the function, with the `sr-workload-boundary` permission
  boundary, logs only to its own log group, plus the caller's `policy_json`.
- Its own log group with a retention and a data-protection policy that masks email
  addresses (a second line behind the redacting logger).
- `publish = true` and an alias `live`; API Gateway and function URLs always target
  `live`.
- Terraform creates the function from a zip, then **ignores code changes**
  (`ignore_changes = [filename, source_code_hash]`): code ships through lane 2
  (`./sr deploy`), config through Terraform. ADR 0004.

Name must match `^sr-[a-z0-9-]+$` (the boundary only allows those names).
