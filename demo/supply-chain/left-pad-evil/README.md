# left-pad-evil (DEMO FIXTURE, harmless)

This package exists only in the supply-chain demo PR and is never merged or published.
Its `postinstall` imitates the first thing a malicious install script does:

1. list environment variables whose names look like credentials (names only, never values)
2. look for `~/.aws/credentials` and a git credential in the checkout
3. make one empty GET to `https://exfil.example.com/collect` (no data sent), a domain the
   CI job's egress policy blocks

Expected on the demo PR (`.github/workflows/supply-chain.yml`): the postinstall does
not run at all (`ignore-scripts=true`), and when run deliberately it finds nothing and
its call is blocked by harden-runner: `RESULT: contained`.
