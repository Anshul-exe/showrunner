"""sr preflight: is the demo ready? Run before an interview, after every phase.

Each check is read-only, except one Bedrock call capped at 5 output tokens (~$0.00001)
and one GitHub App token mint. Nothing secret is printed: the App key is read from SSM
into memory, used to sign a JWT, and dropped.

    PASS  ready
    WARN  works, but the demo will look different (e.g. no model: deterministic triage)
    FAIL  fix before demoing (exit code 1)
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import appconfig
import common

sys.path.insert(0, str(common.ROOT / "ops" / "incident"))

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
API_BUDGET_S = 3.0
BUDGET_WARN_SHARE = 0.8
REPO = "Anshul-exe/showrunner"


@dataclass
class Result:
    status: str
    name: str
    detail: str


# ------------------------------------------------------------------ pure evaluations
def eval_alarms(alarms: list[dict], env: str) -> Result:
    mine = [a for a in alarms if f"-{env}-" in a["AlarmName"]]
    bad = [f"{a['AlarmName']}={a['StateValue']}" for a in mine if a["StateValue"] == "ALARM"]
    if not mine:
        return Result(FAIL, "alarms", f"no sr-*-{env}-* alarms found")
    if bad:
        return Result(FAIL, "alarms", "in ALARM: " + ", ".join(bad))
    nodata = [a["AlarmName"] for a in mine if a["StateValue"] == "INSUFFICIENT_DATA"]
    note = f" ({len(nodata)} INSUFFICIENT_DATA: normal with no traffic)" if nodata else ""
    return Result(PASS, "alarms", f"{len(mine)} alarms, none in ALARM{note}")


def eval_runtime_config(customer: str, flags: dict, chaos: dict) -> Result:
    off = {k: v for k, v in {**flags, **chaos}.items()
           if k in {**appconfig.DEFAULTS["flags"], **appconfig.DEFAULTS["chaos"]}
           and {**appconfig.DEFAULTS["flags"], **appconfig.DEFAULTS["chaos"]}[k] != v}
    if off:
        return Result(FAIL, f"config {customer}", f"not at defaults: {off} (run ./sr demo reset)")
    return Result(PASS, f"config {customer}", "flags and chaos at defaults")


# The $1 budget that proved alert email delivery (bootstrap `enable_smoke_budget`) is
# meant to be exceeded; it isn't a spending signal (switched off 19/11/2026).
IGNORED_BUDGETS = {"showrunner-smoke-test"}


def eval_budgets(budgets: list[dict]) -> list[Result]:
    out = []
    for b in budgets:
        if b["BudgetName"] in IGNORED_BUDGETS:
            continue
        limit = float(b["BudgetLimit"]["Amount"])
        spent = float(b.get("CalculatedSpend", {}).get("ActualSpend", {}).get("Amount", 0))
        forecast = float(b.get("CalculatedSpend", {}).get("ForecastedSpend", {}).get("Amount", 0) or 0)
        status = FAIL if spent >= limit else WARN if max(spent, forecast) >= BUDGET_WARN_SHARE * limit else PASS
        out.append(Result(status, f"budget {b['BudgetName']}",
                          f"${spent:.2f} spent of ${limit:.2f} this month (forecast ${forecast:.2f})"))
    return out or [Result(FAIL, "budgets", "no budgets: phase 0 creates them")]


def eval_quota(concurrency: int) -> Result:
    if concurrency <= 10:
        return Result(WARN, "lambda quota",
                      f"concurrency {concurrency}: demo traffic is capped at 4 rps (raise it: Service Quotas)")
    return Result(PASS, "lambda quota", f"concurrency {concurrency}")


def eval_nat(instances: list[dict], routes: list[dict]) -> Result:
    healthy = [i["InstanceId"] for i in instances if i["LifecycleState"] == "InService" and i["HealthStatus"] == "Healthy"]
    default = next((r for r in routes if r.get("DestinationCidrBlock") == "0.0.0.0/0"), None)
    if not healthy:
        return Result(FAIL, "nat", "no healthy NAT instance in the ASG (private subnets have no internet)")
    if not default or default.get("State") != "active" or default.get("InstanceId") not in healthy:
        target = default.get("InstanceId") if default else "missing"
        return Result(FAIL, "nat", f"private default route -> {target}, healthy NAT is {healthy[0]} (blackhole?)")
    return Result(PASS, "nat", f"{healthy[0]} InService, private 0.0.0.0/0 -> it")


NAT_AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"


def eval_nat_ami(running: str | None, latest: str) -> Result:
    """The NAT AMI is pinned (ADR 0003): say when AWS has published a newer one, so it's
    patched on purpose in a PR, never by surprise in a demo-day apply."""
    if running == latest:
        return Result(PASS, "nat ami", f"{running} (pinned; the latest AL2023 arm64)")
    return Result(WARN, "nat ami", f"pinned {running}; AWS published {latest}. Patch in a PR "
                  "(platform/shared/variables.tf nat_ami_id), not on a demo day: it replaces the NAT (~2-3 min)")


# ------------------------------------------------------------------ live checks
def check_apis(env: str) -> list[Result]:
    out = []
    for c in common.customers(env):
        url = common.customer_params(c, env)["api_url"] + "/catalog"
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 (our own API URL from SSM)
                code = r.status
                r.read()
        except urllib.error.HTTPError as e:
            code = e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            out.append(Result(FAIL, f"api {c}", f"GET /catalog failed: {type(e).__name__}"))
            continue
        s = time.perf_counter() - t0
        status = PASS if code == 200 and s < API_BUDGET_S else WARN if code == 200 else FAIL
        out.append(Result(status, f"api {c}", f"GET /catalog {code} in {s:.2f} s (cold start if > 1 s)"))
    return out


def check_alarms(env: str) -> list[Result]:
    alarms = []
    for page in common.client("cloudwatch").get_paginator("describe_alarms").paginate(AlarmNamePrefix="sr-"):
        alarms += page["MetricAlarms"]
    return [eval_alarms(alarms, env)]


def check_config(env: str) -> list[Result]:
    return [eval_runtime_config(c, appconfig.deployed(c, env, "flags")[0], appconfig.deployed(c, env, "chaos")[0])
            for c in common.customers(env)]


def check_nat(env: str) -> list[Result]:
    shared = common.shared_params(env)
    asg = common.client("autoscaling").describe_auto_scaling_groups(AutoScalingGroupNames=[shared["nat_asg_name"]])
    instances = asg["AutoScalingGroups"][0]["Instances"] if asg["AutoScalingGroups"] else []
    ec2 = common.client("ec2")
    rt = ec2.describe_route_tables(RouteTableIds=[shared["private_route_table_id"]])
    out = [eval_nat(instances, rt["RouteTables"][0]["Routes"])]
    if instances:
        res = ec2.describe_instances(InstanceIds=[i["InstanceId"] for i in instances])["Reservations"]
        running = next((i["ImageId"] for r in res for i in r["Instances"]), None)
        latest = common.client("ssm").get_parameter(Name=NAT_AMI_PARAM)["Parameter"]["Value"]
        out.append(eval_nat_ami(running, latest))
    return out


def eval_network(drift: dict) -> Result:
    if drift["drift"]:
        return Result(FAIL, "cache network rule", f"{drift['cache_sg']} is missing {drift['missing_ports']} from the Lambda "
                      "SG (S3 still active? ./sr demo s3 stop)")
    return Result(PASS, "cache network rule", "Lambda -> Valkey 6379-6380 in place, as Terraform declares")


def check_network(env: str) -> list[Result]:
    import scenarios
    cache_sg, lambda_sg = scenarios._s3_target(env)
    return [eval_network(scenarios.network.sg_rule_drift(common.client("ec2"), cache_sg, lambda_sg))]


def check_incident_loop(env: str) -> list[Result]:
    sfn = common.client("stepfunctions")
    arn = common.client("ssm").get_parameter(Name="/showrunner/ops/incident/state_machine_arn")["Parameter"]["Value"]
    running = sfn.list_executions(stateMachineArn=arn, statusFilter="RUNNING", maxResults=10)["executions"]
    if running:
        return [Result(WARN, "incident loop", f"{len(running)} execution(s) running (waiting for /approve?): "
                       + ", ".join(e["name"] for e in running))]
    return [Result(PASS, "incident loop", "state machine deployed, nothing running")]


def check_github_app(env: str) -> list[Result]:
    from incident.github_app import GitHubApp  # noqa: PLC0415 (only preflight needs it)

    ssm = common.client("ssm")
    ids = common.params("/showrunner/ops/github-app")
    pem = ssm.get_parameter(Name="/showrunner/secret/github-app/private-key", WithDecryption=True)["Parameter"]["Value"]
    try:
        gh = GitHubApp(ids["app-id"], ids["installation-id"], pem, REPO)
        repo = gh.api("GET", "")
        open_incidents = gh.api("GET", "/issues?labels=incident&state=open&per_page=20")
    except Exception as e:  # noqa: BLE001 (report, never print the key or token)
        return [Result(FAIL, "github app", f"token mint or API call failed: {type(e).__name__}")]
    finally:
        del pem
    out = [Result(PASS, "github app", f"installation token minted, repo {repo['full_name']} reachable")]
    if open_incidents:
        out.append(Result(WARN, "open incidents", ", ".join(f"#{i['number']}" for i in open_incidents)
                          + " still open (close them so the demo starts clean)"))
    return out


def check_bedrock(env: str) -> list[Result]:
    model = common.client("ssm").get_parameter(Name="/showrunner/bootstrap/bedrock_model_id")["Parameter"]["Value"]
    try:
        r = common.client("bedrock-runtime").converse(
            modelId=model, messages=[{"role": "user", "content": [{"text": "Reply with: ok"}]}],
            inferenceConfig={"maxTokens": 5, "temperature": 0})
    except Exception as e:  # noqa: BLE001
        msg = str(e).rsplit(": ", 1)[-1][:80]
        return [Result(WARN, "bedrock", f"{model}: {type(e).__name__} ({msg}); the loop uses deterministic triage")]
    u = r["usage"]
    return [Result(PASS, "bedrock", f"{model} answered ({u['inputTokens']} in / {u['outputTokens']} out tokens)")]


def check_budgets(env: str) -> list[Result]:
    acct = common.check_account()
    return eval_budgets(common.client("budgets").describe_budgets(AccountId=acct)["Budgets"])


def check_quota(env: str) -> list[Result]:
    return [eval_quota(common.client("lambda").get_account_settings()["AccountLimit"]["ConcurrentExecutions"])]


CHECKS: list[Callable[[str], list[Result]]] = [
    check_apis, check_alarms, check_config, check_network, check_nat, check_incident_loop, check_github_app,
    check_bedrock, check_budgets, check_quota,
]


def run(env: str, as_json: bool = False) -> int:
    results: list[Result] = []
    for check in CHECKS:
        try:
            results += check(env)
        except Exception as e:  # noqa: BLE001 (one broken check mustn't hide the others)
            results.append(Result(FAIL, check.__name__.removeprefix("check_"), f"check crashed: {type(e).__name__}: {e}"[:200]))
    if as_json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        print(f"sr preflight {env} at {datetime.now(timezone.utc):%d/%m/%Y %H:%M} UTC")
        for r in results:
            print(f"  {r.status:4}  {r.name:22} {r.detail}")
        fails, warns = sum(r.status == FAIL for r in results), sum(r.status == WARN for r in results)
        print(f"{'NOT READY' if fails else 'READY'}: {fails} fail, {warns} warn")
    return 1 if any(r.status == FAIL for r in results) else 0


def reset(env: str) -> int:
    """Every customer's flags and chaos back to the Terraform defaults (dev only)."""
    for c in common.customers(env):
        for profile in ("chaos", "flags"):
            current, _ = appconfig.deployed(c, env, profile)
            changes = {k: v for k, v in appconfig.DEFAULTS[profile].items() if current.get(k) != v}
            if not changes:
                print(f"  {c} {profile}: already at defaults")
                continue
            r = appconfig.set_keys(c, env, profile, changes, "sr demo reset: back to defaults")
            print(f"  {c} {profile}: {json.dumps(changes)} -> deployment #{r['deployment']} ({r['seconds']} s)")
    import scenarios
    scenarios.s3("stop", env)  # S3: the cache's security-group rule, if it was revoked
    print("reset done; run ./sr preflight")
    return 0
