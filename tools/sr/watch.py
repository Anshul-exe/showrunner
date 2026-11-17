"""`sr demo watch`: per-dependency p95 and alarm states, from CloudWatch, every 15 s."""

from __future__ import annotations

import datetime as dt
import time

from common import client, customer_params

DEPS = ["valkey", "dynamodb", "vendor-recs"]


def snapshot(customers: list[str], env: str) -> tuple[dict, dict]:
    cw = client("cloudwatch")
    now = dt.datetime.now(dt.timezone.utc)
    queries, keys = [], {}
    for i, c in enumerate(customers):
        for j, d in enumerate(DEPS):
            qid = f"m{i}_{j}"
            keys[qid] = (c, d)
            queries.append({
                "Id": qid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "Showrunner", "MetricName": "DependencyLatency",
                        "Dimensions": [{"Name": "customer", "Value": c}, {"Name": "env", "Value": env}, {"Name": "dependency", "Value": d}],
                    },
                    "Period": 60, "Stat": "p95",
                },
            })
    resp = cw.get_metric_data(MetricDataQueries=queries, StartTime=now - dt.timedelta(minutes=6), EndTime=now, ScanBy="TimestampDescending")
    p95 = {}
    for r in resp["MetricDataResults"]:
        if r["Values"]:
            p95[keys[r["Id"]]] = (r["Values"][0], r["Timestamps"][0])
    names = [n for c in customers for n in customer_params(c, env)["alarm_names"].split(",")]
    alarms = {a["AlarmName"]: a["StateValue"] for a in cw.describe_alarms(AlarmNames=names)["MetricAlarms"]}
    return p95, alarms


def fmt_ms(v: float) -> str:
    return f"{v / 1000:.2f} s" if v >= 1000 else f"{v:.1f} ms"


def watch(customers: list[str], env: str, interval: int = 15, once: bool = False) -> None:
    while True:
        p95, alarms = snapshot(customers, env)
        print(f"\n{time.strftime('%H:%M:%S')}  dependency p95, last complete minute (CloudWatch)")
        print(f"  {'customer':<15}" + "".join(f"{d:>14}" for d in DEPS))
        for c in customers:
            cells = [fmt_ms(p95[(c, d)][0]) if (c, d) in p95 else "-" for d in DEPS]
            print(f"  {c:<15}" + "".join(f"{x:>14}" for x in cells))
        firing = [f"{n} {s}" for n, s in sorted(alarms.items()) if s != "OK"]
        print("  alarms: " + (", ".join(firing) if firing else "all OK"), flush=True)
        if once:
            return
        time.sleep(interval)
