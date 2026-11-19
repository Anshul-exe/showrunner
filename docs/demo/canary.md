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

## S2: a bad release, rolled back by the canary (dev, 19/11/2026)

Scenario S2 from the design: a build that **fails 20% of searches**, deployed through
the normal canary on acme-tv under traffic. The drill
([`tools/deploy/s2_drill.py`](../../tools/deploy/s2_drill.py)) builds catalog-api from
main (the same SHA-256 as live v6: `3sDaJk7L...PdPE=`, so the build is reproducible),
then a temp copy whose `/search` raises on 20% of requests (`7IRB4G20...gmB7Q=`). The
fault exists only in that temp zip. A probe sent 5 requests/s to `/search` **from 90 s
before the canary** and read `x-sr-version` on every response, so the split was measured
this time. Raw record: [s2/s2-20261119T1355.json](s2/s2-20261119T1355.json).

| UTC | Event |
|---|---|
| 13:55:23 | probe starts: 5 rps, `/search` on acme-tv |
| 13:56:53 | canary starts: `v6 -> v7: 10% for 5 min, watching sr-acme-tv-dev-canary-errors, sr-acme-tv-dev-api-latency` |
| 13:57:03 | v7 (the bad build) published by digest; alias `live` = v6 + 10% v7 |
| 13:59:25 | `sr-acme-tv-dev-canary-errors` -> ALARM (API 5xx >= 1/min in 2 of 3 minutes: 7 and 8) |
| ~13:59:27 | **rolled back after 154 s**: `live` 100% v6 (the tool reads the alarms every 10 s) |
| 13:59:37 | v6's code re-published as **v8** (same digest), so the newest version is good code again |
| 14:02:25 | `canary-errors` -> OK |
| 14:02:40 | probe ends |

What the viewers saw, per minute (probe, client side):

| Minute | v6 (old) | v7 (bad) | v7's share |
|---|---|---|---|
| 13:56 | 300 requests, 0 errors | - | 0% |
| 13:57 | 265, 0 errors | 34, **7 errors** | 11.4% |
| 13:58 | 270, 0 errors | 30, **8 errors** | 10.0% |
| 13:59 | 283, 0 errors | 16, **3 errors** (until the rollback) | 5.4% |
| 14:00-14:02 | 807, 0 errors | - | 0% |

- **The split was measured:** 80 of 898 requests (8.9%) went to v7 during the canary,
  10-11% in the full minutes, against a configured 10%.
- **The blast radius:** 18 failed searches, all on v7 (22.5% of its 80, against 20%
  designed), i.e. **2% of all searches for 2.5 minutes**. Without the canary the same build
  would have failed 20% of every search until someone noticed.
- **v6 served 2,101 requests with 0 errors** before, during and after.
- **No incident, no page:** `canary-errors` is `incident: false` (its consumer is the
  deploy tool); no issue was opened and no other alarm changed state.
- **Terraform afterwards:** `customers/acme-tv/dev` plans `function_version = "6" -> "8"`
  (one in-place change). That is the restore working: v8 is v6's code. Without it the
  next apply would have moved `live` to **v7, the rejected build**. The next apply moves
  the alias to the same code.

Not covered by this run: the latency rollback alarm (a slow build), and a runner killed
mid-canary (tested against a fake AWS only: the tool's exception path rolls back).
