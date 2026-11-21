"""Demo scenarios (dev only). Each one is a trigger and a reset, nothing else: the
platform's own alarms, dashboards and (day 2) incident loop do the rest.

S1, slow external vendor (the P9 story): +1500 ms on acme-tv's vendor calls. The
vendor alarm should fire in about 3 minutes; the per-dependency view points at the
vendor while the legacy view shows rising cache connections.

S3, security-group drift (networking/access): someone removes the Lambda -> Valkey
rule by hand. Every customer in the env loses the cache once its connections recycle
(at most 120 s, CACHE_MAX_CONN_AGE_S); requests fall back to DynamoDB, slower, and
Valkey errors are counted. The fix is the change lane: re-apply shared/<env> through
the pipeline. `stop` restores the exact rule Terraform declares, so the plan is clean.

S4, DNS: a flags deployment points acme-tv's vendor client at a typo'd host
(`lambda-ur` instead of `lambda-url`, under AWS's on.aws domain, so it is NXDOMAIN).
Vendor calls fail; the fix is the runtime lane: roll back that AppConfig deployment.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import appconfig
import common
import traffic

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops" / "lib"))
from opslib import network  # noqa: E402

S1_CUSTOMER = "acme-tv"
S1_LATENCY_MS = 1500


S1_TRAFFIC_MIN_LEFT = 20  # alarm ~4 min + approval + recovery checks (10 min): the loop needs viewers
# The analysis compares the last 3 min with the minutes before the alarm and needs at least
# 3 of them (ops/incident evidence.py MIN_BASELINE_POINTS, ADR 0015); the first minute is
# cold starts. 5 min of traffic before S1 (alarm ~2 min later) leaves enough clean minutes.
S1_TRAFFIC_MIN_AGE = 5


def traffic_warning(customer: str, now: float | None = None) -> str | None:
    """S1 needs traffic to the customer until the recovery check is done; issue #15
    (19/11/2026) escalated because the demo traffic ended before the fix went live.
    It also needs traffic *before* the alarm: issue #24 (21/11/2026) started S1 after
    ~2 min of traffic, and every hypothesis came back "not enough history"."""
    st = traffic.running(now=now)
    now = now or time.time()
    if st is None:
        return (f"WARNING: no `sr demo traffic` running. Start it first, for {customer}, for 40 minutes: "
                f"./sr demo traffic --minutes 40 --rps 3 --customer {customer}")
    if customer not in st["customers"]:
        return f"WARNING: the running traffic doesn't include {customer} (it sends to {', '.join(st['customers'])})."
    warnings = []
    left = (st["ends_at"] - now) / 60
    if left < S1_TRAFFIC_MIN_LEFT:
        warnings.append(f"WARNING: the traffic ends in {left:.0f} min ({time.strftime('%H:%M', time.localtime(st['ends_at']))}); "
                        f"the loop needs viewers for ~{S1_TRAFFIC_MIN_LEFT} min to check the recovery. "
                        "Restart it with --minutes 40, or the check will say 'no traffic'.")
    if "started_at" in st and (age := (now - st["started_at"]) / 60) < S1_TRAFFIC_MIN_AGE:
        ready = time.strftime('%H:%M', time.localtime(st["started_at"] + S1_TRAFFIC_MIN_AGE * 60))
        warnings.append(f"WARNING: the traffic has run for only {age:.0f} min; the analysis needs a baseline "
                        f"(the minutes before the alarm), or every hypothesis says 'not enough history'. "
                        f"Stop S1 (./sr demo s1 stop) and start it again after {ready}.")
    return "\n".join(warnings) or None


def s1(action: str | None, env: str) -> int:
    if action == "start":
        if warning := traffic_warning(S1_CUSTOMER):
            print(warning)
        r = appconfig.set_keys(S1_CUSTOMER, env, "chaos", {"vendor_latency_ms": S1_LATENCY_MS}, "S1 start: vendor +1500 ms")
        print(f"S1 started: {S1_CUSTOMER} vendor latency +{S1_LATENCY_MS} ms (deployment #{r['deployment']}, live in {r['seconds']} s).")
        print("Run traffic (./sr demo traffic) and watch (./sr demo watch): the vendor alarm should fire in ~3 min.")
        return 0
    if action == "stop":
        for profile, changes in (("chaos", {"vendor_latency_ms": 0, "vendor_error_rate": 0}), ("flags", {"vendor_fallback": False})):
            current, _ = appconfig.deployed(S1_CUSTOMER, env, profile)
            if all(current.get(k) == v for k, v in changes.items()):
                continue
            r = appconfig.set_keys(S1_CUSTOMER, env, profile, changes, "S1 stop: back to defaults")
            print(f"{profile}: {json.dumps(r['after'])} (deployment #{r['deployment']})")
        print("S1 stopped: chaos off, flags at defaults.")
        return 0
    print("usage: sr demo s1 start|stop")
    return 2


# ------------------------------------------------------------------ S3
# The rule modules/cache declares (keep in step with it: `stop` must leave no drift).
S3_PORTS = (6379, 6380)
S3_DESCRIPTION = "Valkey (6379 primary, 6380 reader) from catalog-api"


def _s3_target(env: str) -> tuple[str, str]:
    shared = common.shared_params(env)
    cache_sg = network.cache_security_group(common.client("elasticache"), shared["cache_name"])
    return cache_sg, shared["lambda_security_group_id"]


def s3(action: str | None, env: str) -> int:
    ec2 = common.client("ec2")
    if action not in ("start", "stop"):
        print("usage: sr demo s3 start|stop")
        return 2
    cache_sg, lambda_sg = _s3_target(env)
    perm = {"IpProtocol": "tcp", "FromPort": S3_PORTS[0], "ToPort": S3_PORTS[1],
            "UserIdGroupPairs": [{"GroupId": lambda_sg, "Description": S3_DESCRIPTION}]}
    drift = network.sg_rule_drift(ec2, cache_sg, lambda_sg)
    if action == "start":
        if drift["drift"]:
            print(f"S3 already started: {cache_sg} is missing {drift['missing_ports']} from {lambda_sg}.")
            return 0
        ec2.revoke_security_group_ingress(GroupId=cache_sg, IpPermissions=[perm])
        print(f"S3 started: revoked tcp {S3_PORTS[0]}-{S3_PORTS[1]} from {lambda_sg} on {cache_sg} (the cache's SG).")
        print("Existing Valkey connections live on until they recycle (<= 120 s); then cache calls fail and "
              "requests fall back to DynamoDB. Run traffic; the dependency-errors alarm fires in ~3-4 min.")
        print("Fix (change lane): re-apply shared/" + env + " through the pipeline, or `./sr demo s3 stop`.")
        return 0
    if not drift["drift"]:
        print(f"S3 not active: {cache_sg} allows {S3_PORTS[0]}-{S3_PORTS[1]} from {lambda_sg}.")
        return 0
    ec2.authorize_security_group_ingress(GroupId=cache_sg, IpPermissions=[perm])
    after = network.sg_rule_drift(ec2, cache_sg, lambda_sg)
    print(f"S3 stopped: rule restored on {cache_sg} exactly as Terraform declares it (drift: {after['drift']}).")
    return 0 if not after["drift"] else 1


# ------------------------------------------------------------------ S4
S4_CUSTOMER = "acme-tv"


def typo_host(url: str) -> str:
    """The vendor's function URL with `lambda-url` -> `lambda-ur`: a believable typo
    whose host is NXDOMAIN (on.aws is AWS-owned, so it can't resolve to anyone else)."""
    if ".lambda-url." not in url:
        raise ValueError(f"not a Lambda function URL: {url}")
    return url.replace(".lambda-url.", ".lambda-ur.", 1).rstrip("/")


def s4(action: str | None, env: str) -> int:
    if action == "start":
        bad = typo_host(common.shared_params(env)["vendor_url"])
        r = appconfig.set_keys(S4_CUSTOMER, env, "flags", {"vendor_url": bad},
                               "S4 start: move the vendor client to the new host")
        print(f"S4 started: {S4_CUSTOMER} flags vendor_url = {bad} (deployment #{r['deployment']}, live in {r['seconds']} s).")
        print("Vendor calls fail (NXDOMAIN) once catalog-api reads the flags (<= 15 s); recs fall back to the cached "
              "copy. Run traffic: dependency-errors fires in ~3 min. Fix (runtime lane): roll back that deployment.")
        return 0
    if action == "stop":
        current, _ = appconfig.deployed(S4_CUSTOMER, env, "flags")
        if not current.get("vendor_url"):
            print("S4 not active: vendor_url is unset.")
            return 0
        r = appconfig.set_keys(S4_CUSTOMER, env, "flags", {"vendor_url": ""}, "S4 stop: back to the shared vendor URL")
        print(f"S4 stopped: vendor_url removed (deployment #{r['deployment']}).")
        return 0
    print("usage: sr demo s4 start|stop")
    return 2
