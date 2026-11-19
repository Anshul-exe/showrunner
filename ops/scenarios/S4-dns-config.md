# S4: a config change with a typo'd host (DNS)

Environment: **dev only.** Customer: acme-tv. Problem it answers: P17 (DNS and
deployment issues).

## What breaks

`./sr demo s4 start` deploys acme-tv's AppConfig `flags` with `vendor_url` set to the
vendor's function URL with one letter missing: `...lambda-ur.us-east-1.on.aws`. The
schema accepts it (only https Lambda function URLs are allowed, and a schema can't
check DNS). `on.aws` is AWS's domain, so the typo can't resolve to anyone else's
server: it's NXDOMAIN.

catalog-api reads the flags every 15 s; from then on every vendor call fails with a
DNS error and recommendations come from the cached copy (`fallback-on-error`).

## What you see

| Where | What |
|---|---|
| Dashboard, per dependency | `vendor-recs` errors; latency *drops* (a failed lookup is fast) |
| Alarms | `sr-acme-tv-dev-dependency-errors` |
| Incident analysis | H1 `config-change`, ranked first because the change points a failing dependency elsewhere: flags deployment #n, minutes before the alarm, changed `vendor_url`; **confirmed** by the Route 53 Resolver query logs (NXDOMAIN for that exact host) |
| Proposed action | `rollback-last-appconfig-deployment` (runtime lane): redeploy the flags version before #n. It beats `vendor-fallback-on` (a workaround) because a confirmed cause is undone first |

## Fix (lane 1, runtime)

`/approve A1` on the incident issue: the loop redeploys the previous flags version.
It refuses if anything was deployed after #n (a human decides then).

## Reset (demo)

    ./sr demo s4 stop      # removes vendor_url (back to the shared stack's URL)

## Needs

The flags schema with `vendor_url` (customer stacks) and the Resolver query logs
(`shared/dev`): both arrive with the CI apply of this change. Until then `s4 start`
is refused by AppConfig's validator.
