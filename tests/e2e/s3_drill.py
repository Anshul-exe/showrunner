"""Usage: AWS_PROFILE=showrunner uv run python tests/e2e/s3_drill.py <out-dir>   (dev only)

S3 drill on dev, built so the alarms can't fire (they need 2 of 3 breaching minutes):
revoke the cache rule, wait for connections to recycle, send 4 requests inside ONE
minute, collect evidence exactly as the incident loop would (while the rule is still
revoked), analyse, then restore. Always restores (finally)."""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("tools/sr", "ops/lib", "ops/incident"):
    sys.path.insert(0, str(ROOT / p))
import common  # noqa: E402
import scenarios  # noqa: E402
from incident import analysis, evidence, render  # noqa: E402
from opslib import network, runbooks  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
log_lines = []


def log(msg):
    line = f"{datetime.now(timezone.utc):%H:%M:%S} {msg}"
    print(line, flush=True)
    log_lines.append(line)


def alarms_state():
    cw = common.client("cloudwatch")
    a = cw.describe_alarms(AlarmNamePrefix="sr-")["MetricAlarms"]
    return {x["AlarmName"]: x["StateValue"] for x in a if "-dev-" in x["AlarmName"]}


def get(url):
    t = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            body = json.loads(r.read())
            return r.status, round(time.monotonic() - t, 2), body
    except urllib.error.HTTPError as e:
        return e.code, round(time.monotonic() - t, 2), {}


env, customer = "dev", "acme-tv"
params = common.customer_params(customer, env)
shared = dict(common.shared_params(env))
shared.setdefault("flow_log_group", "/sr/sr-dev/vpc-flow-logs")  # SSM output comes with the shared/dev apply
api = params["api_url"].rstrip("/")

before = alarms_state()
deadline = time.monotonic() + 15 * 60
while any(v == "ALARM" for v in before.values()):  # never start while anything is in ALARM
    assert time.monotonic() < deadline, f"alarms still in ALARM: {before}"
    time.sleep(30)
    before = alarms_state()
log(f"alarms before: {sorted(set(before.values()))}")
restored = False
try:
    t_start = datetime.now(timezone.utc)
    scenarios.s3("start", env)
    log("rule revoked; waiting 130 s for warm connections to pass their 120 s max age (no traffic)")
    time.sleep(130)
    # start the burst 5 s into a fresh minute, finish well inside it
    now = datetime.now(timezone.utc)
    wait = 65 - now.second if now.second > 5 else 5 - now.second
    time.sleep(wait)
    burst = []
    t_burst = datetime.now(timezone.utc)
    for path in ("/titles/t01", "/titles/t02", "/catalog", "/titles/t01/recs"):
        status, secs, body = get(api + path)
        burst.append({"path": path, "status": status, "seconds": secs,
                      "source": body.get("source") if isinstance(body, dict) else None})
        log(f"GET {path} -> {status} in {secs} s (source={burst[-1]['source']})")
    t_burst_end = datetime.now(timezone.utc)
    assert t_burst_end.minute == t_burst.minute, "burst crossed a minute boundary"
    log(f"burst done inside minute {t_burst:%H:%M}")

    # The loop would be woken by the alarm; here the alarm is simulated at the burst end.
    alarm = {"name": f"sr-{customer}-{env}-dependency-errors", "kind": "dependency-errors", "customer": customer,
             "env": env, "iso": t_burst_end.isoformat(), "time": f"{t_burst_end:%H:%M:%S} UTC",
             "reason": "DRILL: simulated (the real alarm needs 2 of 3 breaching minutes; the drill used one)"}

    # Wait for flow logs (60 s aggregation + delivery) and CloudTrail (5-15 min).
    trail = common.client("cloudtrail")
    deadline = time.monotonic() + 15 * 60
    seen_trail = None
    while time.monotonic() < deadline:
        ev = trail.lookup_events(LookupAttributes=[{"AttributeKey": "EventName", "AttributeValue": "RevokeSecurityGroupIngress"}],
                                 StartTime=t_start - timedelta(minutes=1), MaxResults=5)["Events"]
        if ev and seen_trail is None:
            seen_trail = datetime.now(timezone.utc)
            log(f"CloudTrail: RevokeSecurityGroupIngress visible {(seen_trail - t_start).total_seconds():.0f} s after the change")
        if seen_trail and datetime.now(timezone.utc) - t_burst_end > timedelta(minutes=6):
            break
        time.sleep(30)

    taken = []

    def take():  # the budget table arrives with the ops-core apply; the drill counts by hand (1 run)
        if taken:
            raise network.Unavailable("drill: 1 run only")
        taken.append(1)

    network.daily_counter = lambda *a, **k: take
    class EC2Spy:  # keeps the analyzer's raw explanations for the record
        def __init__(self, c):
            self.c, self.raw = c, []

        def __getattr__(self, name):
            fn = getattr(self.c, name)
            if name != "describe_network_insights_analyses":
                return fn

            def wrapped(**kw):
                r = fn(**kw)
                self.raw.append(r["NetworkInsightsAnalyses"][0].get("Explanations"))
                return r
            return wrapped

    spy = EC2Spy(common.client("ec2"))
    clients = evidence.Clients(common.client("cloudwatch"), common.client("appconfig"), common.client("lambda"),
                               common.client("cloudtrail"), ec2=spy,
                               elasticache=common.client("elasticache"), logs=common.client("logs"),
                               dynamodb=object())
    t0 = time.monotonic()
    items = evidence.collect(clients, alarm, params, shared, end=t_burst_end + timedelta(minutes=5))
    log(f"evidence: {len(items)} items in {time.monotonic() - t0:.1f} s (incl. Reachability Analyzer)")

    actions = runbooks.safe_actions(runbooks.load_all(ROOT / "ops" / "runbooks")["dependency-errors"])
    hyps = analysis.deterministic_triage(items, actions)
    for i, h in enumerate(hyps, 1):
        h["id"] = f"H{i}"
    result = analysis.verify(hyps, items)
    choice = analysis.choose_action(hyps, result, actions, items)
    proposal, rec = (choice, None) if choice and runbooks.executable(choice["spec"]) else (None, choice)
    flags = next((e["data"]["flags"] for e in items if e["kind"] == "runtime_flags"), {})
    body = render.analysis(items, {"mode": "rules", "reason": "drill: deterministic triage, no model call",
                                   "hypotheses": hyps}, result, proposal, flags, recommendation=rec)
    (OUT / "analysis.md").write_text(body + "\n")
    (OUT / "evidence.json").write_text(json.dumps(items, indent=2, default=str) + "\n")
    (OUT / "reachability-explanations.json").write_text(json.dumps(spy.raw[-1:], indent=2, default=str) + "\n")
    log(f"analysis: H1={hyps[0]['component']}, verdicts={[v['verdict'] for v in result['verdicts']]}, "
        f"action={(choice or {}).get('action_id')}")
finally:
    t_fix = time.monotonic()
    rc = scenarios.s3("stop", env)
    restored = rc == 0
    log(f"restore: rc={rc} in {time.monotonic() - t_fix:.1f} s")
    time.sleep(5)
    after = [get(api + "/titles/t01") for _ in range(2)]
    log(f"after restore: {[(s, t, b.get('source')) for s, t, b in after]}")
    log(f"alarms after: {alarms_state()}")
    (OUT / "drill-log.txt").write_text("\n".join(log_lines) + "\n")
    (OUT / "burst.json").write_text(json.dumps(locals().get("burst", []), indent=2) + "\n")
