# Lane-2 canary: the first real run (dev, 19/11/2026)

What it shows: new catalog-api code reaching 10% of requests for 5 minutes, then
everyone, with the rollback alarms watched the whole time, and only a zip that CI built
and attested on `main` being deployed, pinned to its digest. Design: [ADR 0016](../adr/0016-lane-2-canary.md).

## The artifact

- Built by `build-deploy` run
  [44427451628](https://github.com/Anshul-exe/showrunner/actions/runs/44427451628) on
  the merge of PR #13 (commit `f0e3667`, 19/11/2026 00:14 UTC): "build and attest" green,
  the deploy job skipped by design (the deploy role's bootstrap change isn't applied yet).
- Provenance checked before deploying:

  ```
  gh attestation verify catalog-api.zip --repo Anshul-exe/showrunner \
    --signer-workflow Anshul-exe/showrunner/.github/workflows/build-deploy.yml \
    --source-ref refs/heads/main
  ```

  passed: predicate `https://slsa.dev/provenance/v1`, signer
  `.github/workflows/build-deploy.yml@refs/heads/main`, subject sha256
  `dec0da264ecb…`. The same command on a copy with one byte appended fails.
- Lambda CodeSha256 of the zip: `3sDaJk7L9fnb…`, passed as `--expect-sha256`; every
  version was published with it (PublishVersion refuses a different upload).

## The run

From a laptop with the owner's dev profile (CI's deploy job is off until bootstrap is
applied), the same tool CI uses:

```
python3 tools/deploy/lambda_deploy.py --zip catalog-api.zip --env dev \
  --customer acme-tv --customer lumen-kids --customer nordic-sports \
  --expect-sha256 3sDaJk7L9fnbOUPIUjCHy6/LMboFy5RTbw+dCs0XdPE=
```

| Customer | live before | new | canary started | promoted after | alarms |
|---|---|---|---|---|---|
| acme-tv | v5 | v6 | 00:19:46 UTC | 320 s | none fired |
| lumen-kids | v3 | v4 | 00:19:46 UTC | 336 s | none fired |
| nordic-sports | v4 | v5 | 00:19:46 UTC | 340 s | none fired |

(The three start one after another, then are watched together: 5 minutes of canary plus
upload, wait and publish per function.)

Mid-canary, 00:24:52 UTC, alias `live` of each function:

```
acme-tv        ["5", {"6": 0.1}]
lumen-kids     ["3", {"4": 0.1}]
nordic-sports  ["4", {"5": 0.1}]
```

After: `live` on v6/v4/v5 with no routing weights; a Terraform plan of
`customers/acme-tv/dev` showed **No changes** (Terraform and the canary agree on `live`);
`./sr preflight` READY at 00:28 UTC.

**Not measured:** the traffic split itself. A probe invoking `live` started at 00:25:03,
too late: 4 answers from the old code, then 56 of 56 from v6 after promotion (responses
carry `x-sr-version`). There was no viewer traffic on dev at the time, so the alarms
had nothing to judge; this run shows the mechanics, not a rollback.

## Not run yet

**S2**, the rollback: a build that fails a share of searches, under traffic, rolled back
by `canary-errors`. Tested against a fake AWS (`tests/test_lambda_deploy.py`), not on dev.
