"""Lane 1 changes: set keys in a customer's AppConfig profile and deploy them.

The new content is the currently *deployed* version with the given keys changed, so two
changes to different keys don't undo each other. AppConfig validates it against the
profile's JSON schema; the deployment uses the env's strategy, so the customer's
dependency-errors alarm can roll it back during the 1-minute bake.

Used by `./sr config set` and by the incident loop's act step.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

DEFAULTS: dict[str, dict[str, Any]] = {
    "flags": {"vendor_fallback": False, "catalog_ttl_s": 300, "recs_ttl_s": 120},
    "chaos": {"vendor_latency_ms": 0, "vendor_error_rate": 0},
}
ACTIVE = {"BAKING", "DEPLOYING", "VALIDATING"}
LIVE = {"BAKING", "COMPLETE", "ROLLED_BACK", "ROLLING_BACK"}


@dataclass(frozen=True)
class Target:
    """One customer env's AppConfig IDs (from SSM /showrunner/<env>/customers/<c>/*)."""

    application_id: str
    environment_id: str
    profile_ids: dict[str, str]  # profile name -> configuration profile ID
    strategy_id: str

    @classmethod
    def from_params(cls, customer: dict[str, str], shared: dict[str, str]) -> "Target":
        return cls(
            customer["appconfig_application_id"],
            customer["appconfig_environment_id"],
            {"flags": customer["flags_profile_id"], "chaos": customer["chaos_profile_id"]},
            shared["appconfig_strategy_id"],
        )


def deployments(ac, t: Target) -> list[dict]:
    return ac.list_deployments(ApplicationId=t.application_id, EnvironmentId=t.environment_id, MaxResults=50)["Items"]


def deployed(ac, t: Target, profile: str) -> tuple[dict[str, Any], int | None]:
    """(content, version) of the profile's last completed deployment, or the defaults."""
    for d in deployments(ac, t):  # newest first
        if d["State"] != "COMPLETE":
            continue
        full = ac.get_deployment(
            ApplicationId=t.application_id, EnvironmentId=t.environment_id, DeploymentNumber=d["DeploymentNumber"]
        )
        if full["ConfigurationProfileId"] != t.profile_ids[profile]:
            continue
        version = int(full["ConfigurationVersion"])
        body = ac.get_hosted_configuration_version(
            ApplicationId=t.application_id, ConfigurationProfileId=t.profile_ids[profile], VersionNumber=version
        )["Content"].read()
        return {**DEFAULTS[profile], **json.loads(body)}, version
    return dict(DEFAULTS[profile]), None


def wait_idle(ac, t: Target, timeout_s: int = 180, on_wait: Callable[[dict], None] | None = None,
              sleep: Callable[[float], None] = time.sleep) -> None:
    """AppConfig allows one deployment per environment at a time (a bake counts)."""
    deadline = time.time() + timeout_s
    while True:
        busy = [d for d in deployments(ac, t) if d["State"] in ACTIVE]
        if not busy:
            return
        if time.time() > deadline:
            raise TimeoutError(f"deployment {busy[0]['DeploymentNumber']} still {busy[0]['State']}")
        if on_wait:
            on_wait(busy[0])
        sleep(10)


def set_keys(ac, t: Target, profile: str, changes: dict[str, Any], description: str,
             on_wait: Callable[[dict], None] | None = None,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Deploy `changes` on top of what's deployed. Returns once the config is live
    (BAKING or COMPLETE); the bake continues in the background."""
    unknown = set(changes) - set(DEFAULTS[profile])
    if unknown:
        raise ValueError(f"unknown keys for {profile}: {sorted(unknown)}")
    current, _ = deployed(ac, t, profile)
    new = {**current, **changes}
    wait_idle(ac, t, on_wait=on_wait, sleep=sleep)
    version = ac.create_hosted_configuration_version(
        ApplicationId=t.application_id, ConfigurationProfileId=t.profile_ids[profile],
        Content=json.dumps(new).encode(), ContentType="application/json", Description=description[:1024],
    )["VersionNumber"]
    started = time.time()
    number = ac.start_deployment(
        ApplicationId=t.application_id, EnvironmentId=t.environment_id,
        ConfigurationProfileId=t.profile_ids[profile], ConfigurationVersion=str(version),
        DeploymentStrategyId=t.strategy_id, Description=description[:1024],
    )["DeploymentNumber"]
    while True:
        state = ac.get_deployment(
            ApplicationId=t.application_id, EnvironmentId=t.environment_id, DeploymentNumber=number
        )["State"]
        if state in LIVE:
            break
        sleep(2)
    return {
        "profile": profile, "before": current, "after": new, "version": version,
        "deployment": number, "state": state, "seconds": round(time.time() - started, 1),
    }
