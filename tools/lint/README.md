# Lint tools (CI only)

checkov and zizmor, hash-locked in their own requirements file because checkov pins
old versions of several packages. They run only in `ci.yml` jobs that have no cloud
access, no secrets and a read-only token, and they only read this repo's own files.
Not shipped in any Lambda package (those come from `services/*/requirements.txt`).

## Open advisories (18/11/2026)

| Package | Advisory | Why it can't be upgraded | Exposure |
|---|---|---|---|
| asteval 1.0.6 | two sandbox escapes (fixed in 1.0.9) | checkov pins `asteval==1.0.6` in every release up to 3.4.5 (12/11/2026) | asteval evaluates expressions from the scanned files: only this repo's Terraform, reviewed in PRs |
| ecdsa 0.19.2 | Minerva timing attack on P-256 (no fix released) | no fixed version exists | a timing side channel on signing; checkov signs nothing with our keys |

Options: accept (dismiss as tolerable risk, with this table as the reason), or drop
checkov from CI and keep tflint + conftest + zizmor. Decision is the repo owner's.
