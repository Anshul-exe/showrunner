# Supply-chain demo: a compromised dependency finds nothing to steal

The risk: "a compromised dependency that leaks CI/CD secrets" (problem map P7). A PR
adds `left-pad-evil`, a local npm package whose `postinstall` script does what a real
malicious package does first: look for credentials in the environment and call home.

It's **harmless by design**: it lists env var *names* only (never values), sends
nothing, and its one request is an empty GET to a domain the job's egress policy
blocks (`exfil.example.com`). The fixture lives only in the demo PR, which stays open
and is never merged.

What `.github/workflows/supply-chain.yml` shows on that PR:

| Layer | Control | Result |
|---|---|---|
| 1 | install scripts off (`.npmrc` `ignore-scripts=true`) | the postinstall never runs |
| 2 | the job holds nothing: `permissions: contents: read`, no `id-token`, no secrets, `persist-credentials: false` | no AWS credentials, no tokens in its environment |
| 2 | harden-runner `egress-policy: block` (allow-list: github.com, api.github.com) | the call to `exfil.example.com` is blocked and listed in the run's insights |

The same holds for the real pipeline: `ci.yml` (which runs third-party code: pip
packages, providers, linters) has no cloud access and blocks egress; only `plan.yml`,
`apply.yml` and `approve.yml` get OIDC tokens, each for a role that trusts only that
workflow (ADR 0009). Python dependencies are hash-locked (`--require-hashes`,
`uv sync --frozen`), and actions are pinned by commit SHA.
