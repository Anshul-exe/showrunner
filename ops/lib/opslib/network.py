"""Network and access evidence tools (Pillar 2b). Used by the incident loop's evidence
step and by `./sr net ...`; plain functions over boto3 clients, so both share them.

    network_changes   CloudTrail: who changed a security group, route or NACL, and when
    flow_rejects      VPC flow logs: REJECTed flows to a port (Logs Insights)
    dns_errors        Route 53 Resolver query logs: NXDOMAIN/SERVFAIL by name (Logs Insights)
    sg_rule_drift     the cache's security group vs the rule Terraform declares
    reachability      Reachability Analyzer path check (capped per day: $0.10 a run)

All read-only except `reachability`, which creates a network-insights path and analysis
(they change nothing in the network) and deletes them afterwards.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# One CloudTrail lookup per name (LookupEvents takes one attribute; filtering by event
# source returns every Describe* call, and "ReadOnly=false" pages through every write
# in the account). LookupEvents allows ~2 calls/s, so the list is kept short.
NETWORK_EVENTS = (
    "RevokeSecurityGroupIngress", "AuthorizeSecurityGroupIngress", "ModifySecurityGroupRules",
    "RevokeSecurityGroupEgress", "CreateRoute", "ReplaceRoute", "DeleteRoute",
    "ReplaceNetworkAclEntry", "DeleteNetworkAclEntry",
)
CACHE_PORTS = (6379, 6380)
REACH_DAILY_CAP = 5
REACH_TAG = "reachability"


class Unavailable(Exception):
    pass


# ------------------------------------------------------------------ CloudTrail
def network_changes(cloudtrail, start: datetime, end: datetime, ignore_user_prefix: str = "sr-ops-") -> list[dict]:
    """Security-group, route and NACL changes in [start, end], newest first, with who.
    CloudTrail delivers events 5-15 min late: an empty answer right after a change
    doesn't mean nothing changed (the live drift check is the reliable signal)."""
    out = []
    for name in NETWORK_EVENTS:
        events = cloudtrail.lookup_events(
            LookupAttributes=[{"AttributeKey": "EventName", "AttributeValue": name}],
            StartTime=start, EndTime=end, MaxResults=20)["Events"]
        for e in events:
            who = e.get("Username") or "?"
            if who.startswith(ignore_user_prefix):
                continue
            resources = [x.get("ResourceName") for x in e.get("Resources", []) if x.get("ResourceName")]
            out.append({"event": name, "at": e["EventTime"], "by": who, "resources": resources})
    return sorted(out, key=lambda x: x["at"], reverse=True)


# ------------------------------------------------------------------ Logs Insights
def insights(logs, group: str, query: str, start: datetime, end: datetime, wait_s: float = 25,
             sleep: Callable[[float], None] = time.sleep) -> list[dict[str, str]]:
    """Run a Logs Insights query and return rows as dicts (fields starting with @ dropped)."""
    qid = logs.start_query(logGroupName=group, startTime=int(start.timestamp()), endTime=int(end.timestamp()),
                           queryString=query, limit=50)["queryId"]
    deadline = time.monotonic() + wait_s
    while True:
        r = logs.get_query_results(queryId=qid)
        if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        if time.monotonic() > deadline:
            logs.stop_query(queryId=qid)
            raise Unavailable(f"Logs Insights query on {group} still running after {wait_s:.0f} s")
        sleep(1)
    if r["status"] != "Complete":
        raise Unavailable(f"Logs Insights query on {group}: {r['status']}")
    return [{f["field"]: f["value"] for f in row if not f["field"].startswith("@")} for row in r["results"]]


FLOW_QUERY = ("filter action = \"REJECT\" and dstPort in [{ports}] "
              "| stats count(*) as flows by srcAddr, dstAddr, dstPort | sort flows desc | limit 10")
DNS_QUERY = ("filter rcode in [\"NXDOMAIN\", \"SERVFAIL\"] "
             "| stats count(*) as queries by query_name, rcode | sort queries desc | limit 10")


def flow_rejects(logs, group: str, start: datetime, end: datetime, ports=CACHE_PORTS, **kw) -> dict[str, Any]:
    rows = insights(logs, group, FLOW_QUERY.format(ports=", ".join(map(str, ports))), start, end, **kw)
    rows = [{**r, "flows": int(float(r.get("flows", 0)))} for r in rows]
    return {"total": sum(r["flows"] for r in rows), "top": rows}


def dns_errors(logs, group: str, start: datetime, end: datetime, **kw) -> dict[str, Any]:
    rows = insights(logs, group, DNS_QUERY, start, end, **kw)
    rows = [{**r, "queries": int(float(r.get("queries", 0)))} for r in rows]
    return {"total": sum(r["queries"] for r in rows), "top": rows}


# ------------------------------------------------------------------ security groups
def cache_security_group(elasticache, cache_name: str) -> str:
    c = elasticache.describe_serverless_caches(ServerlessCacheName=cache_name)["ServerlessCaches"][0]
    return c["SecurityGroupIds"][0]


def sg_rule_drift(ec2, cache_sg: str, client_sg: str, ports=CACHE_PORTS) -> dict[str, Any]:
    """Does the cache SG still let the Lambda SG in on the cache ports? (What Terraform
    declares in modules/cache: one ingress rule, 6379-6380 from the Lambda SG.)"""
    perms = ec2.describe_security_groups(GroupIds=[cache_sg])["SecurityGroups"][0]["IpPermissions"]
    covered = set()
    for p in perms:
        if p.get("IpProtocol") not in ("tcp", "-1"):
            continue
        if not any(g.get("GroupId") == client_sg for g in p.get("UserIdGroupPairs", [])):
            continue
        lo, hi = p.get("FromPort", 0), p.get("ToPort", 65535)
        covered |= {port for port in ports if p.get("IpProtocol") == "-1" or lo <= port <= hi}
    missing = [p for p in ports if p not in covered]
    return {"cache_sg": cache_sg, "client_sg": client_sg, "expected_ports": list(ports),
            "missing_ports": missing, "drift": bool(missing), "rules": len(perms)}


# ------------------------------------------------------------------ Reachability Analyzer
def _enis(ec2, group_id: str, kind: str | None = None) -> list[dict]:
    filters = [{"Name": "group-id", "Values": [group_id]}, {"Name": "status", "Values": ["in-use"]}]
    nis = ec2.describe_network_interfaces(Filters=filters)["NetworkInterfaces"]
    return [n for n in nis if kind is None or n.get("InterfaceType") == kind]


def pick_path(ec2, client_sg: str, cache_sg: str) -> tuple[str, str] | None:
    """A Lambda ENI and a cache endpoint ENI, in the same subnet when possible."""
    src, dst = _enis(ec2, client_sg, "lambda"), _enis(ec2, cache_sg)
    if not src or not dst:
        return None
    for s in src:
        for d in dst:
            if s["SubnetId"] == d["SubnetId"]:
                return s["NetworkInterfaceId"], d["NetworkInterfaceId"]
    return src[0]["NetworkInterfaceId"], dst[0]["NetworkInterfaceId"]


def reachability(ec2, src_eni: str, dst_eni: str, port: int, take: Callable[[], None],
                 wait_s: float = 45, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One Reachability Analyzer run. `take()` enforces the daily cap (raises Unavailable)."""
    take()
    tags = [{"Key": "sr", "Value": REACH_TAG}]
    path = ec2.create_network_insights_path(
        Source=src_eni, Destination=dst_eni, Protocol="tcp", DestinationPort=port,
        TagSpecifications=[{"ResourceType": "network-insights-path", "Tags": tags}],
    )["NetworkInsightsPath"]["NetworkInsightsPathId"]
    analysis = None
    try:
        analysis = ec2.start_network_insights_analysis(
            NetworkInsightsPathId=path,
            TagSpecifications=[{"ResourceType": "network-insights-analysis", "Tags": tags}],
        )["NetworkInsightsAnalysis"]["NetworkInsightsAnalysisId"]
        t0, deadline = time.monotonic(), time.monotonic() + wait_s
        while True:
            a = ec2.describe_network_insights_analyses(NetworkInsightsAnalysisIds=[analysis])["NetworkInsightsAnalyses"][0]
            if a["Status"] != "running":
                break
            if time.monotonic() > deadline:
                raise Unavailable(f"Reachability Analyzer still running after {wait_s:.0f} s")
            sleep(3)
        if a["Status"] != "succeeded":
            raise Unavailable(f"Reachability Analyzer: {a['Status']} {a.get('StatusMessage', '')}".strip())
        blocked = []
        for e in a.get("Explanations", []) or []:
            comp = (e.get("Component") or e.get("SecurityGroup") or {}).get("Id")
            sg = (e.get("SecurityGroup") or {}).get("Id")
            blocked.append({"code": e.get("ExplanationCode"), "component": comp, "security_group": sg})
        return {"reachable": bool(a.get("NetworkPathFound")), "port": port, "blocked_by": blocked[:5],
                "seconds": round(time.monotonic() - t0, 1)}
    finally:  # nothing left behind (the result is in the evidence/audit record)
        try:
            if analysis:
                ec2.delete_network_insights_analysis(NetworkInsightsAnalysisId=analysis)
            ec2.delete_network_insights_path(NetworkInsightsPathId=path)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            pass


def daily_counter(ddb, table: str, cap: int = REACH_DAILY_CAP, kind: str = REACH_TAG,
                  now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Callable[[], None]:
    """A `take()` for reachability(): conditional increment of `<kind>#<day>` in the
    agent-budget table; over the cap or no table = no run."""
    def take() -> None:
        day = now().strftime("%Y-%m-%d")
        try:
            ddb.update_item(
                TableName=table, Key={"pk": {"S": f"{kind}#{day}"}},
                UpdateExpression="ADD calls :one SET expires_at = :exp",
                ConditionExpression="attribute_not_exists(calls) OR calls < :cap",
                ExpressionAttributeValues={":one": {"N": "1"}, ":cap": {"N": str(cap)},
                                           ":exp": {"N": str(int((now() + timedelta(days=7)).timestamp()))}},
            )
        except Exception as exc:  # noqa: BLE001
            if "ConditionalCheckFailed" in type(exc).__name__ + str(exc):
                raise Unavailable(f"daily cap of {cap} Reachability Analyzer runs reached") from None
            raise Unavailable(f"run counter unavailable ({type(exc).__name__}); no run without it") from None
    return take
