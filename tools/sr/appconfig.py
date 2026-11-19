"""Lane 1 changes from the operator's laptop. The logic lives in ops/lib/opslib so the
incident loop's approved actions run exactly the same code (appconfig_change.py)."""

from __future__ import annotations

import json
import sys
from typing import Any

from common import ROOT, client, customer_params, shared_params

sys.path.insert(0, str(ROOT / "ops" / "lib"))
from opslib import appconfig_change as change  # noqa: E402

DEFAULTS = change.DEFAULTS


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def target(customer: str, env: str) -> change.Target:
    return change.Target.from_params(customer_params(customer, env), shared_params(env))


def _progress(d: dict) -> None:
    print(f"  waiting for deployment #{d['DeploymentNumber']} ({d['State']})...", flush=True)


def deployed(customer: str, env: str, profile: str) -> tuple[dict[str, Any], int | None]:
    return change.deployed(client("appconfig"), target(customer, env), profile)


def wait_idle(customer: str, env: str, timeout_s: int = 180) -> None:
    change.wait_idle(client("appconfig"), target(customer, env), timeout_s, on_wait=_progress)


def set_keys(customer: str, env: str, profile: str, changes: dict[str, Any], description: str) -> dict:
    try:
        result = change.set_keys(client("appconfig"), target(customer, env), profile, changes, description,
                                 on_wait=_progress)
    except change.RolledBack as exc:  # issue #17: never report a rolled-back change as done
        from common import die

        die(f"{customer} {profile}: {exc}\n  See ops/runbooks/dependency-errors.md "
            "(\"A fix rolled back by AppConfig\").")
    import undo  # if this change undid an incident's mitigation, say so on the incident

    undo.after_change(customer, env, result, DEFAULTS[profile], description)
    import demo_windows  # chaos changes are demo windows for problem management

    demo_windows.after_change(customer, env, profile)
    return {"customer": customer, **result}
