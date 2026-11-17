import io
import json
from pathlib import Path

import pytest

from opslib import appconfig_change, frontmatter, runbooks

ROOT = Path(__file__).resolve().parents[2]


def test_frontmatter_parse_and_hash():
    doc = frontmatter.parse("---\na: 1\n---\nbody\n")
    assert doc.meta == {"a": 1} and doc.body == "body\n" and len(doc.sha256) == 64
    with pytest.raises(ValueError):
        frontmatter.parse("no front matter")


def test_alarm_names_with_hyphenated_customers():
    kinds = {"vendor-latency", "api-latency", "dependency-errors"}
    ref = runbooks.parse_alarm_name("sr-nordic-sports-dev-vendor-latency", kinds)
    assert (ref.customer, ref.env, ref.kind) == ("nordic-sports", "dev", "vendor-latency")
    assert runbooks.parse_alarm_name("sr-acme-tv-dev-api-latency", kinds).kind == "api-latency"
    assert runbooks.parse_alarm_name("someone-elses-alarm", kinds) is None
    assert runbooks.parse_alarm_name("sr-acme-tv-staging-api-latency", kinds) is None


def test_every_runbook_action_is_well_formed():
    books = runbooks.load_all(ROOT / "ops" / "runbooks")
    assert set(books) == {"vendor-latency", "api-latency", "dependency-errors"}
    for name, doc in books.items():
        for a in runbooks.safe_actions(doc):
            assert a["applies_to"] in {"vendor-recs", "valkey", "dynamodb", "config-change"}, (name, a["id"])
            assert a.get("blast_radius") and a.get("rollback"), (name, a["id"])
            if runbooks.executable(a):
                assert set(a["appconfig"]["set"]) <= set(appconfig_change.DEFAULTS[a["appconfig"]["profile"]])


class FakeAppConfig:
    def __init__(self, busy=0):
        self.busy, self.created, self.started = busy, [], []

    def list_deployments(self, **kw):
        items = [{"DeploymentNumber": 5, "State": "COMPLETE"}]
        if self.busy:
            self.busy -= 1
            items.insert(0, {"DeploymentNumber": 6, "State": "BAKING"})
        return {"Items": items}

    def get_deployment(self, DeploymentNumber, **kw):
        if DeploymentNumber == 5:
            return {"ConfigurationProfileId": "flags-id", "ConfigurationVersion": "4", "State": "COMPLETE"}
        return {"State": "BAKING"}

    def get_hosted_configuration_version(self, **kw):
        return {"Content": io.BytesIO(b'{"vendor_fallback": false, "catalog_ttl_s": 60}')}

    def create_hosted_configuration_version(self, **kw):
        self.created.append(json.loads(kw["Content"]))
        return {"VersionNumber": 5}

    def start_deployment(self, **kw):
        self.started.append(kw)
        return {"DeploymentNumber": 7}


T = appconfig_change.Target("app", "env", {"flags": "flags-id", "chaos": "chaos-id"}, "strat")


def test_set_keys_merges_onto_deployed_and_waits_for_the_bake():
    ac, waits = FakeAppConfig(busy=3), []  # 1 list for the read, then 2 busy polls
    r = appconfig_change.set_keys(ac, T, "flags", {"vendor_fallback": True}, "test", on_wait=waits.append, sleep=lambda s: None)
    assert ac.created == [{"vendor_fallback": True, "catalog_ttl_s": 60, "recs_ttl_s": 120}]  # other keys kept
    assert ac.started[0]["DeploymentStrategyId"] == "strat" and len(waits) == 2
    assert r["before"]["vendor_fallback"] is False and r["deployment"] == 7


def test_unknown_keys_are_refused():
    with pytest.raises(ValueError):
        appconfig_change.set_keys(FakeAppConfig(), T, "flags", {"delete_everything": True}, "x", sleep=lambda s: None)
