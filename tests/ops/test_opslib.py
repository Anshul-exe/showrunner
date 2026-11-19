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
            assert a["applies_to"] in {"vendor-recs", "valkey", "dynamodb", "config-change", "network"}, (name, a["id"])
            assert a.get("blast_radius") and a.get("rollback"), (name, a["id"])
            assert a["lane"] in {"runtime", "change"}, (name, a["id"])
            if a["lane"] == "change":  # recommended for a human, never executed by the loop
                assert not runbooks.executable(a) and a.get("pipeline"), (name, a["id"])
            elif "set" in a["appconfig"]:
                assert runbooks.executable(a)
                assert set(a["appconfig"]["set"]) <= set(appconfig_change.DEFAULTS[a["appconfig"]["profile"]])
            else:
                assert runbooks.executable(a) and a["appconfig"]["rollback"] is True, (name, a["id"])


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


class HistoryAppConfig(FakeAppConfig):
    """Flags v3 (good) deployed as #20, then v4 (vendor_url typo) as #21; chaos #19 between."""
    VERSIONS = {3: b'{"vendor_fallback": false, "catalog_ttl_s": 300, "recs_ttl_s": 120}',
                4: b'{"vendor_fallback": false, "catalog_ttl_s": 300, "recs_ttl_s": 120, "vendor_url": "https://x.lambda-ur.us-east-1.on.aws"}'}
    DEPLOYS = {21: ("flags-id", "4"), 20: ("flags-id", "3"), 19: ("chaos-id", "2")}

    def list_deployments(self, **kw):
        return {"Items": [{"DeploymentNumber": n, "State": "COMPLETE", "StartedAt": None} for n in self.DEPLOYS]}

    def get_deployment(self, DeploymentNumber, **kw):
        if DeploymentNumber in self.DEPLOYS:
            pid, ver = self.DEPLOYS[DeploymentNumber]
            return {"ConfigurationProfileId": pid, "ConfigurationVersion": ver, "State": "COMPLETE"}
        return {"State": "BAKING"}

    def get_hosted_configuration_version(self, VersionNumber, **kw):
        return {"Content": io.BytesIO(self.VERSIONS[VersionNumber])}


def test_rollback_redeploys_the_previous_version_of_the_cited_deployment():
    ac = HistoryAppConfig()
    ac.get_hosted_configuration_version = lambda **kw: {"Content": io.BytesIO(
        HistoryAppConfig.VERSIONS[kw.get("VersionNumber", 4)])}
    r = appconfig_change.rollback(ac, T, "flags", 21, "incident x A1", sleep=lambda s: None)
    assert ac.started[0]["ConfigurationVersion"] == "3" and ac.created == []  # no new version: the old one
    assert r["rolled_back"] == 21 and r["version"] == 3 and r["after"]["vendor_url"] == ""
    assert r["before"]["vendor_url"].endswith("lambda-ur.us-east-1.on.aws")


def test_rollback_refuses_when_something_was_deployed_after_the_cited_one():
    with pytest.raises(ValueError, match="not #20"):
        appconfig_change.rollback(HistoryAppConfig(), T, "flags", 20, "x", sleep=lambda s: None)


def test_optional_key_at_its_default_is_not_written():
    """vendor_url="" is the default: written as absent, so an env whose schema predates
    the key still validates."""
    ac = FakeAppConfig()
    appconfig_change.set_keys(ac, T, "flags", {"vendor_url": ""}, "x", sleep=lambda s: None)
    assert "vendor_url" not in ac.created[0]
    appconfig_change.set_keys(ac, T, "flags", {"vendor_url": "https://a.on.aws"}, "x", sleep=lambda s: None)
    assert ac.created[1]["vendor_url"] == "https://a.on.aws"
