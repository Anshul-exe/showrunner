"""Record when an incident's mitigation is undone (18/11/2026, after issue #6).

The incident loop records the mitigation (`act` row: the AppConfig keys it changed,
before and after). Undoing it later (`sr demo s1 stop`, `sr demo reset`,
`sr config set`) was invisible on the incident: the post-incident review's first
follow-up, "undo the mitigation and record when", had no answer on the record.

Now every operator change to `flags` goes through `after_change()`. It finds the
customer env's latest mitigation that hasn't been undone, and if the keys it changed are
back to their pre-incident values, it writes an `undo` row to the audit table and
comments on the incident's issue (closed or not).

`./sr incident undo-check <customer>` does the same for a change already made (a
backfill, or a change made outside sr).

Not covered (production would be event-driven: EventBridge on AppConfig deployments):
a change made in the console, or AppConfig rolling the mitigation back by itself. The
check command records those after the fact.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Attr

from common import ROOT, client, params, session

import sys

sys.path.insert(0, str(ROOT / "ops" / "incident"))
from incident.audit import Audit  # noqa: E402

ISSUE_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)")


# ------------------------------------------------------------------ pure logic
def changed_keys(act_output: dict) -> dict[str, tuple[Any, Any]]:
    """{key: (before, after)} for the keys the mitigation actually changed."""
    before, after = act_output.get("before", {}), act_output.get("after", {})
    return {k: (before.get(k), after[k]) for k in after if before.get(k) != after[k]}


def is_undone(act_output: dict, current: dict) -> bool:
    keys = changed_keys(act_output)
    return bool(keys) and all(current.get(k) == before for k, (before, _) in keys.items())


def open_mitigation(rows: list[dict]) -> dict | None:
    """The latest `act` row of one incident's rows, unless an `undo` row follows it."""
    rows = sorted(rows, key=lambda r: r["sk"])
    acts = [i for i, r in enumerate(rows) if r["step"] == "act"]
    if not acts:
        return None
    last = acts[-1]
    if any(r["step"] == "undo" for r in rows[last + 1:]):
        return None
    return rows[last]


def issue_of(rows: list[dict]) -> tuple[str, int] | None:
    for r in rows:
        m = ISSUE_URL.search(str(r.get("approval_comment", "")))
        if m:
            return m.group(1), int(m.group(2))
    return None


def comment_body(profile: str, act_output: dict, deployment: int, defaults: dict, by: str, how: str,
                 undone_at: str, recorded_at: str) -> str:
    keys = changed_keys(act_output)
    diff = ", ".join(f"`{k}: {json.dumps(after)} -> {json.dumps(before)}`" for k, (before, after) in keys.items())
    to = "defaults" if all(defaults.get(k) == before for k, (before, _) in keys.items()) else "pre-incident values"
    late = "" if undone_at[:16] == recorded_at[:16] or "recorded" in by else f" (recorded {recorded_at} UTC)"
    return (f"**Mitigation undone**: `{profile}` back to {to} ({diff}), AppConfig deployment #{deployment}, "
            f"at {undone_at} UTC by {by} (change: \"{how}\"){late}.\n\n"
            "This answers the post-incident review's first follow-up. If the dependency is still "
            "unhealthy, its alarm fires again and the loop opens a new incident.")


# ------------------------------------------------------------------ AWS / GitHub
def _audit() -> Audit:
    table = params("/showrunner/ops/incident")["audit_table"]
    return Audit(session().resource("dynamodb").Table(table))


def _incident_rows(audit: Audit, customer: str, env: str) -> list[list[dict]]:
    """Every incident of this customer env that has an `act` row, newest first."""
    ids, kwargs = set(), {"FilterExpression": Attr("step").eq("act") & Attr("pk").begins_with(f"{customer}-{env}-"),
                          "ProjectionExpression": "pk"}
    while True:
        resp = audit.t.scan(**kwargs)
        ids |= {i["pk"] for i in resp["Items"]}
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return sorted((audit.rows(i) for i in ids), key=lambda rows: max(r["sk"] for r in rows), reverse=True)


def operator() -> str:
    if shutil.which("gh"):
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return "the operator"


def _comment(repo: str, issue: int, body: str) -> str:
    if not shutil.which("gh"):
        return "gh not installed: comment not posted (the audit row is written)"
    r = subprocess.run(["gh", "issue", "comment", str(issue), "--repo", repo, "--body", body],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else f"comment failed: {r.stderr.strip()[:200]}"


def record_if_undone(customer: str, env: str, profile: str, current: dict, deployment: int, defaults: dict,
                     how: str, undone_at: datetime | None = None, by: str | None = None) -> str | None:
    """Write the `undo` row + issue comment if this change undid an open mitigation."""
    audit = _audit()
    for rows in _incident_rows(audit, customer, env):
        act = open_mitigation(rows)
        if not act:
            continue
        out = json.loads(act["output"])
        if out.get("profile") != profile or not is_undone(out, current):
            return None  # the latest open mitigation is still in effect
        now = datetime.now(timezone.utc)
        at = (undone_at or now).strftime("%Y-%m-%d %H:%M:%S")
        by = by or operator()
        where = issue_of(rows)
        body = comment_body(profile, out, deployment, defaults, by, how, at, now.strftime("%Y-%m-%d %H:%M:%S"))
        note = _comment(*where, body) if where else "no issue found for the incident"
        incident_id = act["pk"]
        audit.record(incident_id, "undo", detail=f"mitigation undone: {profile} back (AppConfig deployment "
                     f"#{deployment}) by {by} via {how}", operator=by, undone_at=at,
                     output={"profile": profile, "keys": {k: v[0] for k, v in changed_keys(out).items()},
                             "deployment": deployment, "comment": note})
        return f"recorded on {incident_id} (issue #{where[1] if where else '?'}): {note}"
    return None


def after_change(customer: str, env: str, result: dict, defaults: dict, how: str) -> None:
    """Called after every operator AppConfig change. Never fails the change itself."""
    try:
        msg = record_if_undone(customer, env, result["profile"], result["after"], result["deployment"], defaults, how)
        if msg:
            print(f"  mitigation undone: {msg}")
    except Exception as exc:  # the change already happened; the record is best effort, loudly
        print(f"  WARNING: couldn't record the undo on the incident ({type(exc).__name__}: {exc})")


def check(customer: str, env: str, target, profile: str, defaults: dict) -> int:
    """`sr incident undo-check`: record an undo that already happened (outside sr, or before this existed)."""
    ac = client("appconfig")
    for d in ac.list_deployments(ApplicationId=target.application_id, EnvironmentId=target.environment_id,
                                 MaxResults=50)["Items"]:
        if d["State"] != "COMPLETE":
            continue
        full = ac.get_deployment(ApplicationId=target.application_id, EnvironmentId=target.environment_id,
                                 DeploymentNumber=d["DeploymentNumber"])
        if full["ConfigurationProfileId"] != target.profile_ids[profile]:
            continue
        body = ac.get_hosted_configuration_version(
            ApplicationId=target.application_id, ConfigurationProfileId=target.profile_ids[profile],
            VersionNumber=int(full["ConfigurationVersion"]))["Content"].read()
        current = {**defaults, **json.loads(body)}
        # Who made that change isn't known here (IAM names stay out of public issues), so
        # the comment says it was recorded after the fact, and by whom.
        msg = record_if_undone(customer, env, profile, current, d["DeploymentNumber"], defaults,
                               (full.get("Description") or "AppConfig deployment").strip()[:80], full["StartedAt"],
                               by=f"an operator (recorded at {datetime.now(timezone.utc):%H:%M} UTC by {operator()} "
                                  "with `sr incident undo-check`)")
        print(msg or f"{customer} {env}: no open mitigation that this {profile} deployment (#{d['DeploymentNumber']}) undid")
        return 0
    print(f"{customer} {env}: no completed {profile} deployment")
    return 0
