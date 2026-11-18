"""Mirror each customer's `chaos` deployments into the audit table as demo windows, so
the post-incident review can tell `sr demo` runs from real alarm firings.

Source of truth is AppConfig's own deployment history (last 50 per environment), so
chaos set in the console is caught too, on the next sync. Idempotent: one row per
deployment, keyed by its start time. Runs after every chaos change made with sr, and
with `./sr incident sync-demo` (backfill).
"""

from __future__ import annotations

import json
from datetime import timedelta

import appconfig
from common import client, params, session

RETENTION = timedelta(days=90)


def chaos_history(customer: str, env: str) -> list[dict]:
    """[{started, number, active}] for the customer env's chaos deployments, oldest first."""
    ac, t = client("appconfig"), appconfig.target(customer, env)
    defaults = appconfig.DEFAULTS["chaos"]
    out = []
    for d in ac.list_deployments(ApplicationId=t.application_id, EnvironmentId=t.environment_id, MaxResults=50)["Items"]:
        if d["State"] not in ("COMPLETE", "BAKING", "DEPLOYING"):
            continue  # rolled back: the chaos never stayed
        full = ac.get_deployment(ApplicationId=t.application_id, EnvironmentId=t.environment_id,
                                 DeploymentNumber=d["DeploymentNumber"])
        if full["ConfigurationProfileId"] != t.profile_ids["chaos"]:
            continue
        body = ac.get_hosted_configuration_version(
            ApplicationId=t.application_id, ConfigurationProfileId=t.profile_ids["chaos"],
            VersionNumber=int(full["ConfigurationVersion"]))["Content"].read()
        content = {**defaults, **json.loads(body)}
        out.append({"started": full["StartedAt"], "number": d["DeploymentNumber"], "active": content != defaults,
                    "content": content})
    return sorted(out, key=lambda x: x["started"])


def sync(customer: str, env: str) -> int:
    table = session().resource("dynamodb").Table(params("/showrunner/ops/incident")["audit_table"])
    n = 0
    for h in chaos_history(customer, env):
        table.put_item(Item={
            "pk": f"DEMO#{customer}#{env}", "sk": h["started"].isoformat(timespec="seconds"),
            "active": h["active"], "deployment": h["number"], "detail": json.dumps(h["content"]),
            "expires_at": int((h["started"] + RETENTION).timestamp()),
        })
        n += 1
    return n


def after_change(customer: str, env: str, profile: str) -> None:
    if profile != "chaos":
        return
    try:
        sync(customer, env)
    except Exception as exc:  # never fail the change itself
        print(f"  WARNING: couldn't record the demo window ({type(exc).__name__}: {exc})")
