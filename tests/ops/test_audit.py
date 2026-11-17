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
