"""triage -> verify -> act through the real handlers, Bedrock refusing (as on a new
account) and AppConfig faked. AWS calls go to moto, never to a real account."""

import json
import boto3
import pytest
from moto import mock_aws

from incident import audit, handlers

from .fixtures import s1
from .test_opslib import FakeAppConfig

ALARM = {"name": "sr-acme-tv-dev-vendor-latency", "customer": "acme-tv", "env": "dev", "kind": "vendor-latency",
         "severity": "sev2", "reason": "r", "time": "2026-11-17 22:20:09", "iso": "2026-11-17T22:20:09.000+0000",
         "runbook_url": "u"}


class RefusingBedrock:
    def converse(self, **kw):
        raise RuntimeError("ValidationException: Operation not allowed")


@pytest.fixture
def env(monkeypatch):
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(TableName="sr-ops-audit", BillingMode="PAY_PER_REQUEST",
                                 KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                                 AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                       {"AttributeName": "sk", "AttributeType": "S"}])
        ssm = boto3.client("ssm", region_name="us-east-1")
        for k, v in {"appconfig_application_id": "app", "appconfig_environment_id": "env", "flags_profile_id": "flags-id",
                     "chaos_profile_id": "chaos-id", "api_url": "https://x.execute-api"}.items():
            ssm.put_parameter(Name=f"/showrunner/dev/customers/acme-tv/{k}", Value=v, Type="String")
        ssm.put_parameter(Name="/showrunner/dev/shared/appconfig_strategy_id", Value="strat", Type="String")
        fake_ac = FakeAppConfig()
        fakes = {"bedrock-runtime": RefusingBedrock(), "appconfig": fake_ac, "ssm": ssm}
        monkeypatch.setattr(handlers, "_client", lambda name: fakes[name])
        monkeypatch.setattr(handlers, "_audit", lambda: audit.Audit(table))
        yield table, fake_ac


def test_loop_without_a_model_still_reaches_the_right_proposal_and_acts_only_on_approval(env):
    table, ac = env
    state = {"alarm": ALARM, "incident": {"id": "inc-1", "issue": 1, "url": "u"}, "evidence": s1()}
    state = handlers.triage_fn({"state": state})
    assert state["triage"]["mode"] == "deterministic" and "Operation not allowed" in state["triage"]["reason"]
    state = handlers.verify_fn({"state": state})
    assert state["proposal"]["action_id"] == "vendor-fallback-on"
    assert next(a for a in state["verification"]["also_checked"] if a["component"] == "valkey")["verdict"] == "refuted"

    state["approval"] = {"decision": "approve", "action": "A1", "approver": "maintainer", "comment_url": "c"}
    state = handlers.act_fn({"state": state})
    assert ac.created[-1]["vendor_fallback"] is True
    assert "approved by maintainer" in ac.started[-1]["Description"]

    rows = {r["step"]: r for r in audit.Audit(table).rows("inc-1")}
    assert set(rows) == {"triage", "verify", "act"}
    assert len(rows["triage"]["instruction_sha256"]) == 64 and rows["act"]["approver"] == "maintainer"
    # why the verifier didn't use the model is on the record (missing on 18/11/2026)
    assert "(model not used: " in rows["verify"]["detail"] and "Operation not allowed" in rows["verify"]["detail"]


def test_act_refuses_anything_but_the_approved_proposal(env):
    state = {"alarm": ALARM, "incident": {"id": "inc-2"}, "proposal": {"id": "A1", "action_id": "vendor-fallback-on"}}
    for approval in ({"decision": "reject", "action": "A1"}, {"decision": "approve", "action": "A2"}):
        with pytest.raises(ValueError):
            handlers.act_fn({"state": {**state, "approval": approval}})
    assert env[1].started == []


def test_alarms_whose_runbook_says_no_incident_open_nothing(monkeypatch):
    """canary-errors is the deploy tool's rollback trigger (ADR 0016): the EventBridge rule
    sees every sr-* alarm, but the loop opens no incident for it."""
    monkeypatch.setenv("RUNBOOK_BASE_URL", "https://x")

    def event(name):
        return {"detail": {"alarmName": name, "state": {"value": "ALARM", "timestamp": "2026-11-19T00:00:00.000+0000"}}}

    assert handlers.parse_alarm(event("sr-acme-tv-dev-canary-errors")) is None
    assert handlers.parse_alarm(event("sr-acme-tv-dev-api-latency"))["kind"] == "api-latency"


class FakeGitHub:
    def __init__(self):
        self.comments, self.labels = [], []

    def comment(self, issue, body):
        self.comments.append(body)
        return f"https://x/{issue}"

    def add_labels(self, issue, labels):
        self.labels += labels


def _escalate(monkeypatch, env, check):
    table, _ = env
    gh = FakeGitHub()
    monkeypatch.setattr(handlers, "_github", lambda: gh)
    monkeypatch.delenv("ALERTS_TOPIC_ARN", raising=False)
    state = {"incident": {"id": "acme-tv-dev-1", "issue": 15, "url": "u"}, "alarm": ALARM, "check": check}
    out = handlers.github({"step": "escalate", "state": state,
                           "reason": "The API p95 did not recover within 10 minutes of the action."})
    return gh, out


def test_no_traffic_escalates_as_unverified_not_as_a_failed_recovery(env, monkeypatch):
    gh, out = _escalate(monkeypatch, env, {"status": "no-traffic", "no_traffic_since": "13:06", "attempts": 10})
    assert "Can't verify recovery: no traffic since 13:06 UTC" in gh.comments[0]
    assert "did not recover" not in gh.comments[0] and gh.labels == ["unverified"]
    assert out["escalated"].startswith("Can't verify")


def test_a_real_failed_recovery_still_escalates_as_one(env, monkeypatch):
    gh, out = _escalate(monkeypatch, env, {"status": "not-recovered", "api_p95_ms": [1600, 1500], "attempts": 10})
    assert "did not recover" in gh.comments[0] and gh.labels == ["escalated"]


def test_a_failed_action_escalates_with_the_reason(env, monkeypatch):
    """The state machine's Catch on Act puts the Lambda error in $.error (issue #17)."""
    table, _ = env
    gh = FakeGitHub()
    monkeypatch.setattr(handlers, "_github", lambda: gh)
    monkeypatch.delenv("ALERTS_TOPIC_ARN", raising=False)
    cause = json.dumps({"errorMessage": "AppConfig rolled back deployment #22: nothing changed. The following "
                        "CloudWatch monitors are in alarm: [sr-acme-tv-dev-dependency-errors]",
                        "errorType": "RolledBack"})
    state = {"incident": {"id": "acme-tv-dev-1", "issue": 17, "url": "u"}, "alarm": ALARM,
             "error": {"Error": "RolledBack", "Cause": cause}}
    handlers.github({"step": "escalate", "state": state,
                     "reason": "The approved action failed to execute (see the execution history)."})
    assert "RolledBack: AppConfig rolled back deployment #22: nothing changed" in gh.comments[0]
