"""Evidence collected by code, each item with an ID (E1, E2, ...).

The AI steps never query anything themselves: they only see these items and may only
cite their IDs. Every number in the incident issue comes from here.

Baseline vs now uses per-minute p95 datapoints over the last 30 minutes: "now" is the
median of the last 3 minutes, "baseline" the median of the minutes that ended before the
degradation could have started (the alarm needs 2 of 3 one-minute periods, so: before
alarm time - 4 min). Fewer than 3 such minutes (traffic only just started) and the
baseline is the dependency's expected p95 instead, marked as such: a median of minutes
that is too short or includes the problem would hide it (seen on 17/11/2026: the only
"before" minute was the first minute of traffic, inflated by cold starts, and made a
1,600 ms vendor look only 1.2x slower).

Changes count as recent within 15 minutes before the alarm (RECENT_CHANGE); older ones
are listed for context (CHANGE_WINDOW) but can't confirm a change hypothesis.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from opslib import appconfig_change, network

DEPENDENCIES = ("valkey", "dynamodb", "vendor-recs")
NAMESPACE = "Showrunner"
WINDOW_MIN = 30
NOW_POINTS = 3
MIN_BASELINE_POINTS = 3
ONSET_LEAD = timedelta(minutes=4)  # 3 evaluation periods of 1 min + 1 min metric delay
CHANGE_WINDOW = timedelta(hours=2)
RECENT_CHANGE = timedelta(minutes=15)
# Upper end of normal p95 per dependency, used only when there's no history before the
# alarm. Measured normal on 17/11/2026: vendor ~95 ms, Valkey ~1.5 ms, DynamoDB ~9 ms.
EXPECTED_P95_MS = {"vendor-recs": 250.0, "valkey": 5.0, "dynamodb": 25.0}
# CloudTrail write events we don't list: the incident loop's own calls, AppConfig
# (its own item), and noise from logging/credentials.
CLOUDTRAIL_IGNORE = ("appconfig.amazonaws.com", "sts.amazonaws.com", "logs.amazonaws.com", "kms.amazonaws.com")


@dataclass
class Clients:
    cloudwatch: Any
    appconfig: Any
    lambda_: Any
    cloudtrail: Any
    ec2: Any = None
    elasticache: Any = None
    logs: Any = None
    dynamodb: Any = None  # the Reachability Analyzer's daily run counter
    budget_table: str = "sr-ops-agent-budget"


def split_series(values: list[float], times: list[datetime] | None = None,
                 onset: datetime | None = None, expected: float | None = None) -> dict[str, Any]:
    """Per-minute values, oldest first -> {now, baseline, baseline_source, points}.

    With times and onset, the baseline is only the minutes before onset; otherwise the
    minutes before the last NOW_POINTS. Too few -> `expected` (if given) or None."""
    if not values:
        return {"now": None, "baseline": expected, "baseline_source": "expected" if expected else None, "points": 0}
    now_pts = values[-NOW_POINTS:]
    if times and onset:
        base_pts = [v for t, v in zip(times, values) if t < onset][: max(len(values) - NOW_POINTS, 0)]
    else:
        base_pts = values[:-NOW_POINTS]
    if len(base_pts) >= MIN_BASELINE_POINTS:
        baseline, source = round(statistics.median(base_pts), 1), f"median of {len(base_pts)} min before the alarm"
    elif expected is not None:
        baseline, source = expected, f"expected p95 (only {len(base_pts)} min of history before the alarm)"
    else:
        baseline, source = None, None
    return {"now": round(statistics.median(now_pts), 1), "baseline": baseline, "baseline_source": source,
            "points": len(values)}


def _metric_data(cw, queries: list[dict], end: datetime, minutes: int = WINDOW_MIN) -> dict[str, tuple[list, list]]:
    """{query id: (timestamps, values)}, oldest first. Timestamps may be empty (fakes)."""
    resp = cw.get_metric_data(
        MetricDataQueries=queries,
        StartTime=end - timedelta(minutes=minutes),
        EndTime=end,
        ScanBy="TimestampAscending",
    )
    return {r["Id"]: (list(r.get("Timestamps", [])), list(r["Values"])) for r in resp["MetricDataResults"]}


def _metric_series(cw, queries: list[dict], end: datetime, minutes: int = WINDOW_MIN) -> dict[str, list[float]]:
    return {k: v for k, (_, v) in _metric_data(cw, queries, end, minutes).items()}


def parse_time(value: str | None) -> datetime | None:
    """The alarm's state-change timestamp ("2026-11-17T22:20:09.000+0000") -> aware UTC."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stat(namespace: str, metric: str, dims: dict[str, str], stat: str, qid: str) -> dict:
    return {
        "Id": qid,
        "MetricStat": {
            "Metric": {"Namespace": namespace, "MetricName": metric,
                       "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
            "Period": 60,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def _qid(dep: str) -> str:
    return dep.replace("-", "_")


def api_id(api_url: str) -> str:
    return api_url.split("//", 1)[1].split(".", 1)[0]


def fmt_ms(v: float | None) -> str:
    return "no data" if v is None else f"{v:,.0f} ms" if v >= 10 else f"{v:,.1f} ms"


class Collector:
    alarm_at: datetime | None = None

    def __init__(self, clients: Clients, customer: str, env: str, params: dict[str, str], shared: dict[str, str]):
        self.c, self.customer, self.env, self.p, self.shared = clients, customer, env, params, shared
        self.items: list[dict[str, Any]] = []

    def add(self, kind: str, summary: str, source: str, **data: Any) -> str:
        eid = f"E{len(self.items) + 1}"
        self.items.append({"id": eid, "kind": kind, "summary": summary, "source": source, "data": data})
        return eid

    # ------------------------------------------------------------------ items
    def alarm(self, alarm: dict) -> None:
        self.alarm_at = parse_time(alarm.get("iso"))
        self.add("alarm", f"{alarm['name']} entered ALARM at {alarm['time']}: {alarm['reason']}",
                 "CloudWatch alarm state change", name=alarm["name"], alarm_kind=alarm["kind"])

    def dependencies(self, end: datetime) -> None:
        dims = {"customer": self.customer, "env": self.env}
        q = []
        for dep in DEPENDENCIES:
            q.append(_stat(NAMESPACE, "DependencyLatency", {**dims, "dependency": dep}, "p95", f"lat_{_qid(dep)}"))
            q.append(_stat(NAMESPACE, "DependencyErrors", {**dims, "dependency": dep}, "Sum", f"err_{_qid(dep)}"))
        q.append(_stat("AWS/ApiGateway", "Latency", {"ApiId": api_id(self.p["api_url"]), "Stage": "$default"}, "p95", "api"))
        data = _metric_data(self.c.cloudwatch, q, end)
        onset = self.alarm_at - ONSET_LEAD if self.alarm_at else None

        def split(qid: str, expected: float | None = None) -> dict[str, Any]:
            times, values = data.get(qid, ([], []))
            return split_series(values, times, onset, expected)

        def base_text(s: dict) -> str:
            return f"{fmt_ms(s['baseline'])} ({s['baseline_source']})" if s["baseline_source"] else "no baseline"

        api = split("api")
        self.add("api_latency",
                 f"API p95 (API Gateway, all routes): {fmt_ms(api['now'])} in the last {NOW_POINTS} min vs "
                 f"{base_text(api)}.",
                 "CloudWatch AWS/ApiGateway Latency p95, 1-min periods, last 30 min", **api)
        for dep in DEPENDENCIES:
            s = split(f"lat_{_qid(dep)}", EXPECTED_P95_MS[dep])
            errors = sum(data.get(f"err_{_qid(dep)}", ([], []))[1][-5:])
            self.add("dependency_latency",
                     f"{dep} p95 (client side, from catalog-api): {fmt_ms(s['now'])} in the last {NOW_POINTS} min vs "
                     f"{base_text(s)}; {errors:.0f} errors in the last 5 min.",
                     "CloudWatch Showrunner/DependencyLatency p95 + DependencyErrors (EMF), 1-min periods",
                     dependency=dep, errors_5m=errors, **s)

    def valkey_server(self, end: datetime) -> None:
        cluster = self.shared.get("cache_name", "")
        q = [_stat("AWS/ElastiCache", "SuccessfulReadRequestLatency", {"clusterId": cluster}, "p99", "read"),
             _stat("AWS/ElastiCache", "SuccessfulWriteRequestLatency", {"clusterId": cluster}, "p99", "write")]
        s = _metric_series(self.c.cloudwatch, q, end, minutes=10)
        read = max(s.get("read", []) or [0]) or None
        write = max(s.get("write", []) or [0]) or None
        us = lambda v: "no data" if v is None else f"{v:,.0f} µs ({v / 1000:.1f} ms)"  # noqa: E731
        self.add("valkey_server_latency",
                 f"Valkey server-side latency, worst minute in the last 10 min: read p99 {us(read)}, write p99 {us(write)}.",
                 f"CloudWatch AWS/ElastiCache {cluster} (serverless; microseconds)",
                 read_p99_us=read, write_p99_us=write)

    def appconfig_changes(self, end: datetime) -> None:
        ac = self.c.appconfig
        app, envid = self.p["appconfig_application_id"], self.p["appconfig_environment_id"]
        flags_id = self.p["flags_profile_id"]
        changes = []
        for d in ac.list_deployments(ApplicationId=app, EnvironmentId=envid, MaxResults=50)["Items"]:
            started = d.get("StartedAt")
            if not started or started < end - CHANGE_WINDOW:
                continue
            full = ac.get_deployment(ApplicationId=app, EnvironmentId=envid, DeploymentNumber=d["DeploymentNumber"])
            if full["ConfigurationProfileId"] != flags_id:
                continue
            changes.append({"deployment": d["DeploymentNumber"], "state": d["State"],
                            "started": started.astimezone(timezone.utc).strftime("%H:%M:%S"),
                            "minutes_before_alarm": self._minutes_before(started, end),
                            "description": (full.get("Description") or "")[:120]})
        text = ("none" if not changes else
                "; ".join(f"#{c['deployment']} at {c['started']} UTC, {c['minutes_before_alarm']:.0f} min before the "
                          f"alarm ({c['state']}): {c['description']}" for c in changes))
        self.add("config_changes",
                 f"Runtime config (AppConfig `flags`) deployments in the last 2 h: {text}. "
                 "(The `chaos` profile is excluded on purpose: it drives the simulated vendor, i.e. the outside "
                 "world, whose changes a real platform team couldn't see.)",
                 f"AppConfig application sr-{self.customer}-{self.env}", changes=changes)

    def _minutes_before(self, t: datetime, end: datetime) -> float:
        return round(((self.alarm_at or end) - t).total_seconds() / 60, 1)

    def runtime_flags(self, end: datetime) -> None:
        target = appconfig_change.Target.from_params(self.p, self.shared)
        content, version = appconfig_change.deployed(self.c.appconfig, target, "flags")
        defaults = appconfig_change.DEFAULTS["flags"]
        off = {k: v for k, v in content.items() if k in defaults and defaults[k] != v}
        self.add("runtime_flags", f"Runtime flags now (AppConfig `flags` v{version}): "
                 + ", ".join(f"{k}={v}" for k, v in content.items()) + "; "
                 + ("all at their defaults." if not off else "differ from defaults: " + ", ".join(off) + "."),
                 f"AppConfig application sr-{self.customer}-{self.env}", flags=content, version=version,
                 at_defaults=not off)

    def deploys(self, end: datetime) -> None:
        lam, fn = self.c.lambda_, self.p["function_name"]
        versions = []
        for page in lam.get_paginator("list_versions_by_function").paginate(FunctionName=fn):
            for v in page["Versions"]:
                if v["Version"] == "$LATEST":
                    continue
                modified = datetime.fromisoformat(v["LastModified"].replace("+0000", "+00:00"))
                if modified >= end - CHANGE_WINDOW:
                    versions.append({"version": v["Version"], "at": modified.strftime("%H:%M:%S"),
                                     "minutes_before_alarm": self._minutes_before(modified, end)})
        live = lam.get_alias(FunctionName=fn, Name="live")["FunctionVersion"]
        text = "none" if not versions else ", ".join(
            f"v{v['version']} at {v['at']} UTC ({v['minutes_before_alarm']:.0f} min before the alarm)" for v in versions)
        self.add("deploys", f"catalog-api versions published in the last 2 h: {text}; alias `live` -> v{live}.",
                 f"Lambda {fn}", versions=versions, live=live)

    def cloudtrail(self, end: datetime) -> None:
        events = self.c.cloudtrail.lookup_events(
            LookupAttributes=[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
            StartTime=end - CHANGE_WINDOW, EndTime=end, MaxResults=50,
        )["Events"]
        counts: dict[str, int] = {}
        for e in events:
            if e.get("EventSource", "") in CLOUDTRAIL_IGNORE or (e.get("Username") or "").startswith("sr-ops-"):
                continue
            key = f"{e['EventSource'].split('.')[0]}:{e['EventName']}"
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
        text = "none" if not top else ", ".join(f"{k} x{n}" for k, n in top)
        self.add("infra_changes",
                 f"Infrastructure write calls in the last 2 h (CloudTrail, newest 50; AppConfig and the incident "
                 f"loop's own calls excluded; delivery lags ~5-15 min): {text}.",
                 "CloudTrail LookupEvents ReadOnly=false", counts=dict(top))


    # ------------------------------------------------------------------ network (Pillar 2b)
    def network(self, end: datetime) -> None:
        """S3's evidence: is the Lambda -> Valkey rule still there (live, vs Terraform's
        declared rule), who changed network config (CloudTrail), are flows to the cache
        port being REJECTed (flow logs, now vs the hour before), and, only when one of
        those says "network", a Reachability Analyzer path check (capped, $0.10 a run)."""
        if self.c.ec2 is None or not self.shared.get("lambda_security_group_id"):
            return
        client_sg = self.shared["lambda_security_group_id"]
        cache_sg = network.cache_security_group(self.c.elasticache, self.shared["cache_name"])
        drift = network.sg_rule_drift(self.c.ec2, cache_sg, client_sg)
        if drift["drift"]:
            text = (f"DRIFT: the cache security group no longer lets catalog-api in on port(s) "
                    f"{', '.join(map(str, drift['missing_ports']))} (Terraform declares 6379-6380 from the Lambda "
                    f"security group; {drift['rules']} rule(s) left).")
        else:
            text = "No drift: the cache security group still allows 6379-6380 from the Lambda security group, as declared."
        self.add("sg_drift", text, f"EC2 DescribeSecurityGroups vs modules/cache (Terraform)", **drift)

        changes = network.network_changes(self.c.cloudtrail, end - CHANGE_WINDOW, end)
        for c in changes:
            c["minutes_before_alarm"] = self._minutes_before(c["at"], end)
            c["at"] = c["at"].astimezone(timezone.utc).strftime("%H:%M:%S")
        text = ("none found (CloudTrail delivers 5-15 min late, so a change in the last minutes may not show yet)"
                if not changes else "; ".join(
                    f"{c['event']} at {c['at']} UTC ({c['minutes_before_alarm']:.0f} min before the alarm) by "
                    f"`{c['by']}` on {', '.join(c['resources'][:2]) or '?'}" for c in changes[:5]))
        self.add("network_changes", f"Security-group, route and NACL changes in the last 2 h: {text}.",
                 "CloudTrail LookupEvents by EventName", changes=changes[:10])

        flows = None
        group = self.shared.get("flow_log_group")
        if group and self.c.logs is not None:
            now = network.flow_rejects(self.c.logs, group, end - NET_RECENT, end)
            base = network.flow_rejects(self.c.logs, group, end - NET_RECENT - NET_BASELINE, end - NET_RECENT)
            rate_now = now["total"] / (NET_RECENT.total_seconds() / 60)
            rate_base = base["total"] / (NET_BASELINE.total_seconds() / 60)
            surge = now["total"] >= 5 and rate_now >= 3 * max(rate_base, 0.1)
            flows = {"now": now["total"], "baseline": base["total"], "rate_now_pm": round(rate_now, 1),
                     "rate_baseline_pm": round(rate_base, 2), "surge": surge, "top": now["top"][:3]}
            self.add("flow_rejects",
                     f"VPC flow logs, REJECTed flows to Valkey (6379/6380): {now['total']} in the last "
                     f"{NET_RECENT.seconds // 60} min ({rate_now:.1f}/min) vs {base['total']} in the hour before "
                     f"({rate_base:.2f}/min){': a surge' if surge else ''}.",
                     f"Logs Insights on {group}", **flows)

        if not (drift["drift"] or (flows and flows["surge"])) or self.c.dynamodb is None:
            return
        pair = network.pick_path(self.c.ec2, client_sg, cache_sg)
        if not pair:
            self.add("reachability", "Reachability Analyzer: not run (no in-use Lambda or cache network interface).",
                     "EC2 Reachability Analyzer", reachable=None)
            return
        try:
            take = network.daily_counter(self.c.dynamodb, self.c.budget_table)
            r = network.reachability(self.c.ec2, pair[0], pair[1], 6379, take)
        except network.Unavailable as exc:
            self.add("reachability", f"Reachability Analyzer: not run ({exc}).", "EC2 Reachability Analyzer",
                     reachable=None)
            return
        where = "; ".join(f"{b['code']} at {b['security_group'] or b['component']}" for b in r["blocked_by"]) or "-"
        self.add("reachability",
                 (f"Reachability Analyzer, catalog-api ENI -> Valkey ENI on 6379: "
                  + ("REACHABLE" if r["reachable"] else f"NOT reachable, blocked: {where}")
                  + f" (analysis took {r['seconds']:.0f} s)."),
                 "EC2 Reachability Analyzer (capped per day)", src=pair[0], dst=pair[1], **r)

    def dns(self, end: datetime) -> None:
        """S4's evidence: names that don't resolve from inside the VPC (Resolver query
        logs), e.g. a typo'd vendor host in a config change."""
        group = self.shared.get("resolver_log_group")
        if not group or self.c.logs is None:
            return
        r = network.dns_errors(self.c.logs, group, end - NET_RECENT, end)
        top = ", ".join(f"`{x.get('query_name', '?').rstrip('.')}` {x.get('rcode')} x{x['queries']}"
                        for x in r["top"][:5]) or "none"
        self.add("dns_errors", f"DNS errors from inside the VPC in the last {NET_RECENT.seconds // 60} min "
                 f"(Route 53 Resolver query logs): {top}.", f"Logs Insights on {group}",
                 total=r["total"], names=[{"name": x.get("query_name", "").rstrip("."), "rcode": x.get("rcode"),
                                           "queries": x["queries"]} for x in r["top"][:10]])

NET_RECENT = timedelta(minutes=10)  # flow-log / DNS "now" window ends at collection time
NET_BASELINE = timedelta(minutes=60)  # the hour before it


def collect(clients: Clients, alarm: dict, params: dict[str, str], shared: dict[str, str],
            end: datetime | None = None) -> list[dict[str, Any]]:
    end = end or datetime.now(timezone.utc)
    col = Collector(clients, alarm["customer"], alarm["env"], params, shared)
    col.alarm(alarm)
    for step in (col.dependencies, col.valkey_server, col.appconfig_changes, col.runtime_flags, col.deploys,
                 col.cloudtrail, col.network, col.dns):
        try:
            step(end)
        except Exception as exc:  # evidence is best effort: a missing item is itself recorded
            col.add("collection_error", f"Could not collect {step.__name__}: {type(exc).__name__}.", "collector")
    return col.items


def recovery(cw, api_url: str, threshold_ms: float, end: datetime | None = None) -> dict[str, Any]:
    """Post-change check on what users see: the last 2 one-minute API p95 values."""
    end = end or datetime.now(timezone.utc)
    q = [_stat("AWS/ApiGateway", "Latency", {"ApiId": api_id(api_url), "Stage": "$default"}, "p95", "api")]
    values = _metric_series(cw, q, end, minutes=5).get("api", [])
    last = [round(v, 1) for v in values[-2:]]
    return {"recovered": len(last) == 2 and all(v < threshold_ms for v in last),
            "api_p95_ms": last, "threshold_ms": threshold_ms, "at": end.strftime("%H:%M:%S")}
