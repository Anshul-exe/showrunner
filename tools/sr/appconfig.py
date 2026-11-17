"""Lane 1 changes: set keys in a customer's AppConfig profile and deploy them.

The new content is the currently *deployed* version with the given keys changed, so
two operators changing different keys don't undo each other. AppConfig validates it
against the profile's JSON schema; the deployment uses the env's strategy, so the
customer's dependency-errors alarm can roll it back during the 1-minute bake.
"""

from __future__ import annotations

import json
import time
from typing import Any

from common import client, customer_params, shared_params

DEFAULTS = {
    "flags": {"vendor_fallback": False, "catalog_ttl_s": 300, "recs_ttl_s": 120},
    "chaos": {"vendor_latency_ms": 0, "vendor_error_rate": 0},
}
ACTIVE = {"BAKING", "DEPLOYING", "VALIDATING"}


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _ids(customer: str, env: str, profile: str) -> tuple[str, str, str]:
    p = customer_params(customer, env)
    return p["appconfig_application_id"], p["appconfig_environment_id"], p[f"{profile}_profile_id"]


def _deployments(app: str, envid: str) -> list[dict]:
    return client("appconfig").list_deployments(ApplicationId=app, EnvironmentId=envid, MaxResults=50)["Items"]


def deployed(customer: str, env: str, profile: str) -> tuple[dict[str, Any], int | None]:
    """(content, version) of the profile's last completed deployment, or defaults."""
    ac = client("appconfig")
    app, envid, prof = _ids(customer, env, profile)
    for d in _deployments(app, envid):  # newest first
        if d["State"] != "COMPLETE":
            continue
        full = ac.get_deployment(ApplicationId=app, EnvironmentId=envid, DeploymentNumber=d["DeploymentNumber"])
        if full["ConfigurationProfileId"] != prof:
            continue
        version = int(full["ConfigurationVersion"])
        body = ac.get_hosted_configuration_version(
            ApplicationId=app, ConfigurationProfileId=prof, VersionNumber=version
        )["Content"].read()
        return {**DEFAULTS[profile], **json.loads(body)}, version
    return dict(DEFAULTS[profile]), None


def wait_idle(customer: str, env: str, timeout_s: int = 180) -> None:
    """One deployment per AppConfig environment at a time (a bake counts)."""
    app, envid, _ = _ids(customer, env, "flags")
    deadline = time.time() + timeout_s
    while True:
        busy = [d for d in _deployments(app, envid) if d["State"] in ACTIVE]
        if not busy:
            return
        if time.time() > deadline:
            raise TimeoutError(f"deployment {busy[0]['DeploymentNumber']} still {busy[0]['State']}")
        print(f"  waiting for deployment #{busy[0]['DeploymentNumber']} ({busy[0]['State']})...", flush=True)
        time.sleep(10)


def set_keys(customer: str, env: str, profile: str, changes: dict[str, Any], description: str) -> dict:
    """Deploy `changes` on top of what's deployed. Returns once the config is live
    (state BAKING or COMPLETE); the bake continues in the background."""
    ac = client("appconfig")
    app, envid, prof = _ids(customer, env, profile)
    current, _ = deployed(customer, env, profile)
    unknown = set(changes) - set(DEFAULTS[profile])
    if unknown:
        raise ValueError(f"unknown keys for {profile}: {sorted(unknown)}")
    new = {**current, **changes}
    wait_idle(customer, env)
    version = ac.create_hosted_configuration_version(
        ApplicationId=app, ConfigurationProfileId=prof,
        Content=json.dumps(new).encode(), ContentType="application/json",
        Description=description[:1024],
    )["VersionNumber"]
    started = time.time()
    dep = ac.start_deployment(
        ApplicationId=app, EnvironmentId=envid, ConfigurationProfileId=prof,
        ConfigurationVersion=str(version),
        DeploymentStrategyId=shared_params(env)["appconfig_strategy_id"],
        Description=description[:1024],
    )
    number = dep["DeploymentNumber"]
    while True:
        state = ac.get_deployment(ApplicationId=app, EnvironmentId=envid, DeploymentNumber=number)["State"]
        if state in ("BAKING", "COMPLETE", "ROLLED_BACK", "ROLLING_BACK"):
            break
        time.sleep(2)
    return {
        "customer": customer, "profile": profile, "before": current, "after": new,
        "version": version, "deployment": number, "state": state,
        "seconds": round(time.time() - started, 1),
    }
