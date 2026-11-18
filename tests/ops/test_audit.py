from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from incident.audit import Audit, elapsed


@pytest.fixture
def table():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        yield ddb.create_table(TableName="sr-ops-audit", BillingMode="PAY_PER_REQUEST",
                               KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                               AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                     {"AttributeName": "sk", "AttributeType": "S"}])


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 17, 22, 20, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


def test_rows_in_order_with_agent_fields(table):
    clock = Clock()
    a = Audit(table, clock)
    a.record("inc-1", "open", detail="issue #1")
    clock.t += timedelta(seconds=40)
    a.record("inc-1", "triage", agent="triage", instruction_sha256="abc", input_tokens=10, cost_usd=0.0001,
             evidence_ids=["E1"], output={"hypotheses": []})
    rows = a.rows("inc-1")
    assert [r["step"] for r in sorted(rows, key=lambda r: r["sk"])] == ["open", "triage"]
    assert elapsed(rows, "open", "triage") == 40.0
    tri = next(r for r in rows if r["step"] == "triage")
    assert tri["instruction_sha256"] == "abc" and tri["output"] == '{"hypotheses": []}'


def test_one_open_incident_per_customer_env_within_30_min(table):
    clock = Clock()
    a = Audit(table, clock)
    assert a.claim("acme-tv", "dev", "inc-1", 1, "u1")
    assert not a.claim("acme-tv", "dev", "inc-2", 2, "u2")       # second alarm: join, don't open
    assert a.open_incident("acme-tv", "dev")["incident_id"] == "inc-1"
    assert a.open_incident("nordic-sports", "dev") is None       # other customers are separate
    clock.t += timedelta(minutes=31)
    assert a.open_incident("acme-tv", "dev") is None             # stale: a new incident next time
    assert a.claim("acme-tv", "dev", "inc-3", 3, "u3")
    a.close("acme-tv", "dev", "inc-1")                           # closing an old one doesn't drop the new
    assert a.open_incident("acme-tv", "dev")["incident_id"] == "inc-3"
    a.close("acme-tv", "dev", "inc-3")
    assert a.open_incident("acme-tv", "dev") is None


# ---------------------------------------------------------------- demo windows (18/11/2026)
from datetime import datetime as _dt, timezone as _tz  # noqa: E402

from incident import audit as audit_mod  # noqa: E402
from incident import render as render_mod  # noqa: E402


def _t(s):
    return _dt.fromisoformat(s).replace(tzinfo=_tz.utc)


# acme-tv's real chaos history on 17-18/11/2026 (AppConfig deployments #2..#15)
DEMO = [{"sk": s, "active": a} for s, a in [
    ("2026-11-17T22:07:38+00:00", True), ("2026-11-17T22:11:56+00:00", False),
    ("2026-11-17T22:18:02+00:00", True), ("2026-11-17T22:28:18+00:00", False),
    ("2026-11-17T23:09:49+00:00", True), ("2026-11-17T23:21:27+00:00", False),
    ("2026-11-18T10:08:10+00:00", True), ("2026-11-18T10:20:28+00:00", False)]]


def test_real_vendor_alarm_history_splits_into_1_real_4_demo():
    firings = [_t("2026-11-17T22:01:03"),  # cold start on first traffic, before any chaos: real
               _t("2026-11-17T22:10:03"), _t("2026-11-17T22:20:03"), _t("2026-11-17T23:11:03"),
               _t("2026-11-18T10:10:03")]
    assert audit_mod.split_firings(firings, DEMO) == (1, 4)


def test_grace_after_chaos_off_then_real_again():
    assert audit_mod.during_demo(_t("2026-11-17T22:20:00"), DEMO[:4])          # on
    assert audit_mod.during_demo(_t("2026-11-17T22:35:00"), DEMO[:4])          # 7 min after off: grace
    assert not audit_mod.during_demo(_t("2026-11-17T22:45:00"), DEMO[:4])      # 17 min after off
    assert not audit_mod.during_demo(_t("2026-11-17T22:00:00"), DEMO)          # before any chaos


def test_problem_line():
    assert render_mod.problem_line("a", 1, 4).endswith("1 real, under the threshold of 3: not labelled.")
    assert "4 of them during `sr demo` chaos (not counted)" in render_mod.problem_line("a", 1, 4)
    assert "labelled `problem-management`" in render_mod.problem_line("a", 3, 2)
    assert render_mod.problem_line("a", 2, 0) == ""
    assert render_mod.problem_line("a", 0, 0) == ""
