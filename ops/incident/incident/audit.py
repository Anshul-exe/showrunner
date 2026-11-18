"""The audit trail (DynamoDB `sr-ops-audit`) and the open-incident record.

One row per step: pk = incident ID, sk = "<ISO time>#<step>". Fields: agent and its
instruction file's SHA-256, model, tokens, cost, the evidence IDs it used, its output,
and (for approve/act) the human who approved. The post-incident review is built from
these rows, so the record and the review can't disagree.

The open-incident record (pk = "OPEN#<customer>#<env>") makes one GitHub issue per
incident: a second alarm for the same customer env within 30 minutes joins it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

REUSE_WINDOW_S = 30 * 60
RETENTION_S = 90 * 24 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


class Audit:
    def __init__(self, table, clock=_now):
        self.t, self.clock = table, clock

    def record(self, incident_id: str, step: str, **fields: Any) -> dict:
        at = self.clock()
        if "output" in fields and not isinstance(fields["output"], str):
            fields["output"] = json.dumps(fields["output"], default=str)[:20000]
        item = {"pk": incident_id, "sk": f"{at.isoformat(timespec='milliseconds')}#{step}", "step": step,
                "at": at.isoformat(timespec="seconds"), "expires_at": int(at.timestamp()) + RETENTION_S,
                **_clean(fields)}
        self.t.put_item(Item=item)
        return item

    def rows(self, incident_id: str) -> list[dict]:
        out, kwargs = [], {"KeyConditionExpression": Key("pk").eq(incident_id)}
        while True:
            resp = self.t.query(**kwargs)
            out += resp["Items"]
            if "LastEvaluatedKey" not in resp:
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # ------------------------------------------------------------ open incidents
    @staticmethod
    def _open_key(customer: str, env: str) -> dict:
        return {"pk": f"OPEN#{customer}#{env}", "sk": "OPEN"}

    def open_incident(self, customer: str, env: str) -> dict | None:
        item = self.t.get_item(Key=self._open_key(customer, env)).get("Item")
        if item and int(item["last_alarm"]) >= int(self.clock().timestamp()) - REUSE_WINDOW_S:
            return item
        return None

    def claim(self, customer: str, env: str, incident_id: str, issue: int, url: str) -> bool:
        """Become the open incident, unless another one claimed it within the window."""
        now = int(self.clock().timestamp())
        try:
            self.t.put_item(
                Item={**self._open_key(customer, env), "incident_id": incident_id, "issue": issue, "url": url,
                      "opened": now, "last_alarm": now, "expires_at": now + 7 * 24 * 3600},
                ConditionExpression="attribute_not_exists(pk) OR last_alarm < :cutoff",
                ExpressionAttributeValues={":cutoff": now - REUSE_WINDOW_S},
            )
            return True
        except self.t.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def touch(self, customer: str, env: str) -> None:
        self.t.update_item(Key=self._open_key(customer, env), UpdateExpression="SET last_alarm = :n",
                           ExpressionAttributeValues={":n": int(self.clock().timestamp())})

    def close(self, customer: str, env: str, incident_id: str) -> None:
        try:
            self.t.delete_item(Key=self._open_key(customer, env), ConditionExpression="incident_id = :i",
                               ExpressionAttributeValues={":i": incident_id})
        except self.t.meta.client.exceptions.ConditionalCheckFailedException:
            pass


# ---------------------------------------------------------------- demo windows
# Chaos (S1 etc.) is injected by `sr`. Its deployments are mirrored here as
# pk = "DEMO#<customer>#<env>", sk = deployment start (ISO), active = chaos not at
# defaults, so problem management can tell test runs from real firings (18/11/2026:
# "fired 5 times in 30 days" were all demo runs).
DEMO_GRACE_S = 10 * 60  # an alarm can still fire this long after chaos is switched off


def demo_rows(table, customer: str, env: str) -> list[dict]:
    out, kwargs = [], {"KeyConditionExpression": Key("pk").eq(f"DEMO#{customer}#{env}")}
    while True:
        resp = table.query(**kwargs)
        out += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return out
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def during_demo(t: datetime, rows: list[dict]) -> bool:
    """Was chaos on at time t (or switched off less than DEMO_GRACE_S before)?"""
    before = [r for r in rows if datetime.fromisoformat(r["sk"]) <= t]
    if not before:
        return False
    last = max(before, key=lambda r: r["sk"])
    if last["active"]:
        return True
    return (t - datetime.fromisoformat(last["sk"])).total_seconds() <= DEMO_GRACE_S


def split_firings(times: list[datetime], rows: list[dict]) -> tuple[int, int]:
    """(real, demo) alarm firings."""
    demo = sum(1 for t in times if during_demo(t, rows))
    return len(times) - demo, demo


def elapsed(rows: list[dict], from_step: str, to_step: str) -> float | None:
    def at(step):
        r = next((r for r in sorted(rows, key=lambda r: r["sk"]) if r["step"] == step), None)
        return datetime.fromisoformat(r["sk"].split("#", 1)[0]) if r else None
    a, b = at(from_step), at(to_step)
    return round((b - a).total_seconds(), 1) if a and b else None
