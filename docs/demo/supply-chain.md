# Supply-chain demo, recorded run (fallback for the live demo)

PR [#4](https://github.com/Anshul-exe/showrunner/pull/4) (open, labelled `demo`, never
merged) adds `left-pad-evil`, a harmless fixture that behaves like a compromised
dependency ([demo/supply-chain/README.md](../../demo/supply-chain/README.md)).
Check run: [supply-chain #44202602602](https://github.com/Anshul-exe/showrunner/actions/runs/44202602602),
17/11/2026 23:49 UTC.

## What the run printed

Layer 1, install scripts off:

```
$ npm config get ignore-scripts
true
$ npm ci --offline --no-audit --no-fund
postinstall did not run (ignore-scripts=true)
```

Layer 2, the postinstall run on purpose, as if scripts were on:

```
left-pad-evil postinstall (harmless demo):
- credential-like env vars: none
- ~/.aws/credentials: none
- git credentials in the checkout: none
- call to https://exfil.example.com/collect: failed (ECONNREFUSED): egress blocked
RESULT: contained
```

harden-runner, same second:

```
Tue, 17 Nov 2026 23:49:06 GMT:domain not allowed: exfil.example.com.
```

## Why it finds nothing

- the job's token is `contents: read` and isn't exported to the environment; no
  `id-token: write`, so no way to get AWS credentials; no secrets referenced
- `persist-credentials: false` leaves no git credential in the checkout
- egress is an allow-list (github.com, api.github.com); everything else is refused and
  reported
- the real pipeline uses the same pattern: only `plan.yml`, `apply.yml` and
  `approve.yml` can request OIDC tokens, each for a role that trusts only that workflow
