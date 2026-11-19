"""Pillar 2b: network evidence tools (opslib.network) and the S3/S4 stories end to end
through triage -> verify -> the proposed action."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from .fixtures import NET_ACTIONS, s1, s3, s4
from incident import analysis, render
from opslib import network, runbooks

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ fakes
class EC2:
    def __init__(self, perms, status="succeeded", found=False):
        self.perms, self.status, self.found = perms, status, found
        self.deleted, self.created = [], 0

    def describe_security_groups(self, GroupIds):
        return {"SecurityGroups": [{"GroupId": GroupIds[0], "IpPermissions": self.perms}]}

    def describe_network_interfaces(self, Filters):
        sg = Filters[0]["Values"][0]
        if sg == "sg-lambda":
            return {"NetworkInterfaces": [{"NetworkInterfaceId": "eni-l1", "SubnetId": "a", "InterfaceType": "lambda"},
                                          {"NetworkInterfaceId": "eni-l2", "SubnetId": "b", "InterfaceType": "lambda"}]}
        return {"NetworkInterfaces": [{"NetworkInterfaceId": "eni-c1", "SubnetId": "b", "InterfaceType": "interface"}]}

    def create_network_insights_path(self, **kw):
        self.created += 1
        assert kw["DestinationPort"] == 6379 and kw["Protocol"] == "tcp"
        return {"NetworkInsightsPath": {"NetworkInsightsPathId": "nip-1"}}

    def start_network_insights_analysis(self, **kw):
        return {"NetworkInsightsAnalysis": {"NetworkInsightsAnalysisId": "nia-1"}}

    def describe_network_insights_analyses(self, **kw):
        a = {"Status": self.status, "NetworkPathFound": self.found}
        if not self.found:  # the documented shape for this code (EC2 API Explanation)
            a["Explanations"] = [{"ExplanationCode": "ENI_SG_RULES_MISMATCH", "SecurityGroups": [{"Id": "sg-cache"}],
                                  "NetworkInterface": {"Id": "eni-c1"}}]
        return {"NetworkInsightsAnalyses": [a]}

    def delete_network_insights_analysis(self, **kw):
        self.deleted.append(kw["NetworkInsightsAnalysisId"])

    def delete_network_insights_path(self, **kw):
        self.deleted.append(kw["NetworkInsightsPathId"])


RULE = {"IpProtocol": "tcp", "FromPort": 6379, "ToPort": 6380, "UserIdGroupPairs": [{"GroupId": "sg-lambda"}]}


class Logs:
    def __init__(self, rows, statuses=("Running", "Complete")):
        self.rows, self.statuses, self.queries, self.stopped = rows, list(statuses), [], False

    def start_query(self, **kw):
        self.queries.append(kw)
        return {"queryId": "q"}

    def get_query_results(self, queryId):
        return {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0], "results": self.rows}

    def stop_query(self, queryId):
        self.stopped = True


class Trail:
    def lookup_events(self, LookupAttributes, **kw):
        name = LookupAttributes[0]["AttributeValue"]
        if name == "RevokeSecurityGroupIngress":
            return {"Events": [
                {"EventName": name, "EventTime": NOW - timedelta(minutes=4), "Username": "alice-console",
                 "Resources": [{"ResourceName": "sg-cache"}]},
                {"EventName": name, "EventTime": NOW - timedelta(minutes=2), "Username": "sr-ops-act",
                 "Resources": []}]}
        return {"Events": []}


class Counter:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def update_item(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail


class ConditionalCheckFailedException(Exception):
    pass


# ------------------------------------------------------------------ opslib.network
def test_sg_rule_drift():
    assert not network.sg_rule_drift(EC2([RULE]), "sg-cache", "sg-lambda")["drift"]
    only_primary = dict(RULE, ToPort=6379)
    d = network.sg_rule_drift(EC2([only_primary]), "sg-cache", "sg-lambda")
    assert d["drift"] and d["missing_ports"] == [6380]
    other_source = dict(RULE, UserIdGroupPairs=[{"GroupId": "sg-other"}])
    assert network.sg_rule_drift(EC2([other_source]), "sg-cache", "sg-lambda")["missing_ports"] == [6379, 6380]
    assert network.sg_rule_drift(EC2([]), "sg-cache", "sg-lambda")["drift"]


def test_network_changes_filters_the_loop_itself_and_sorts_newest_first():
    out = network.network_changes(Trail(), NOW - timedelta(hours=2), NOW)
    assert [(c["event"], c["by"], c["resources"]) for c in out] == [
        ("RevokeSecurityGroupIngress", "alice-console", ["sg-cache"])]


def test_flow_rejects_and_dns_errors_parse_insights_rows():
    logs = Logs([[{"field": "srcAddr", "value": "10.0.1.5"}, {"field": "dstAddr", "value": "10.0.2.9"},
                  {"field": "dstPort", "value": "6379"}, {"field": "flows", "value": "12"},
                  {"field": "@ptr", "value": "x"}]])
    r = network.flow_rejects(logs, "/sr/dev/vpc-flow-logs", NOW - timedelta(minutes=10), NOW, sleep=lambda s: None)
    assert r == {"total": 12, "top": [{"srcAddr": "10.0.1.5", "dstAddr": "10.0.2.9", "dstPort": "6379", "flows": 12}]}
    assert "dstPort in [6379, 6380]" in logs.queries[0]["queryString"]
    dns = network.dns_errors(Logs([[{"field": "query_name", "value": "x.lambda-ur.us-east-1.on.aws."},
                                    {"field": "rcode", "value": "NXDOMAIN"}, {"field": "queries", "value": "7"}]]),
                             "/sr/dev/resolver", NOW - timedelta(minutes=10), NOW, sleep=lambda s: None)
    assert dns["total"] == 7 and dns["top"][0]["rcode"] == "NXDOMAIN"


def test_insights_query_that_never_finishes_is_stopped():
    logs = Logs([], statuses=("Running",))
    with pytest.raises(network.Unavailable):
        network.insights(logs, "g", "q", NOW, NOW, wait_s=0, sleep=lambda s: None)
    assert logs.stopped


def test_pick_path_prefers_the_same_subnet():
    assert network.pick_path(EC2([]), "sg-lambda", "sg-cache") == ("eni-l2", "eni-c1")


def test_reachability_reports_the_block_and_cleans_up():
    ec2 = EC2([], found=False)
    r = network.reachability(ec2, "eni-l2", "eni-c1", 6379, take=lambda: None, sleep=lambda s: None)
    assert r["reachable"] is False and r["blocked_by"][0] == {
        "code": "ENI_SG_RULES_MISMATCH", "component": "eni-c1", "security_group": "sg-cache"}
    assert ec2.deleted == ["nia-1", "nip-1"]


def test_reachability_failed_analysis_still_cleans_up():
    ec2 = EC2([], status="failed")
    with pytest.raises(network.Unavailable):
        network.reachability(ec2, "a", "b", 6379, take=lambda: None, sleep=lambda s: None)
    assert ec2.deleted == ["nia-1", "nip-1"]


def test_reachability_daily_cap_and_no_counter_means_no_run():
    ec2 = EC2([])
    capped = network.daily_counter(Counter(ConditionalCheckFailedException("x")), "t", now=lambda: NOW)
    with pytest.raises(network.Unavailable, match="daily cap of 2"):
        network.reachability(ec2, "a", "b", 6379, take=capped)
    broken = network.daily_counter(Counter(RuntimeError("AccessDenied")), "t", now=lambda: NOW)
    with pytest.raises(network.Unavailable, match="no run without it"):
        network.reachability(ec2, "a", "b", 6379, take=broken)
    assert ec2.created == 0  # nothing created, nothing paid
    c = Counter()
    network.daily_counter(c, "sr-ops-agent-budget", now=lambda: NOW)()
    kw = c.calls[0]
    assert kw["Key"] == {"pk": {"S": "reachability#2026-11-19"}} and kw["ExpressionAttributeValues"][":cap"] == {"N": "2"}


# ------------------------------------------------------------------ S3: security-group drift
def _ids(hyps):
    for i, h in enumerate(hyps, 1):
        h["id"] = f"H{i}"
    return hyps


def test_s3_drift_is_the_first_hypothesis_confirmed_and_recommends_a_reapply():
    ev = s3()
    hyps = _ids(analysis.deterministic_triage(ev, NET_ACTIONS))
    assert hyps[0]["component"] == "network" and "E10" in hyps[0]["cites"]
    result = analysis.verify(hyps, ev)
    v = {x["hypothesis"]: x for x in result["verdicts"]}
    assert v["H1"]["verdict"] == "confirmed"
    assert [c["name"] for c in v["H1"]["checks"]] == ["sg-rule", "flow-rejects", "reachability"]
    choice = analysis.choose_action(hyps, result, NET_ACTIONS, ev)
    assert choice["action_id"] == "reapply-shared-stack"
    assert not runbooks.executable(choice["spec"])  # lane change: shown, never executed


def test_no_drift_refutes_a_network_hypothesis():
    ev = s1() + [{"id": "E10", "kind": "sg_drift", "summary": "No drift", "source": "t",
                  "data": {"drift": False, "missing_ports": []}},
                 {"id": "E11", "kind": "flow_rejects", "summary": "0", "source": "t",
                  "data": {"now": 0, "baseline": 3, "surge": False}}]
    hyps = _ids([{"component": "network", "statement": "the network", "cites": ["E10"], "action": None}])
    assert analysis.verify(hyps, ev)["verdicts"][0]["verdict"] == "refuted"


# ------------------------------------------------------------------ S4: a typo'd host in the flags
def test_s4_config_change_ranks_first_and_rollback_is_proposed_with_the_cited_deployment():
    ev = s4()
    hyps = _ids(analysis.deterministic_triage(ev, NET_ACTIONS))
    assert hyps[0]["component"] == "config-change" and "vendor_url" in hyps[0]["statement"]
    result = analysis.verify(hyps, ev)
    v = {x["hypothesis"]: x for x in result["verdicts"]}
    assert v["H1"]["verdict"] == "confirmed"
    assert any(c["name"] == "changed-host-resolves" and c["result"] == "pass" for c in v["H1"]["checks"])
    choice = analysis.choose_action(hyps, result, NET_ACTIONS, ev)
    # the rollback (undo the cause) beats vendor-fallback-on (work around the symptom)
    assert choice["action_id"] == "rollback-last-appconfig-deployment"
    assert choice["params"] == {"deployment": 21} and runbooks.executable(choice["spec"])
    body = render.analysis(ev, {"mode": "rules", "reason": "test", "hypotheses": hyps}, result, choice,
                           ev[7]["data"]["flags"])
    assert "before deployment #21" in body and "/approve A1" in body


def test_s4_without_a_dns_record_is_not_confirmed_by_checks():
    ev = s4(dns_hit=False)
    hyps = _ids(analysis.deterministic_triage(ev, NET_ACTIONS))
    v = analysis.verify(hyps, ev)["verdicts"][0]
    assert v["verdict"] == "inconclusive"  # the model may weigh in; the checks alone don't act


def test_change_lane_recommendation_is_rendered_for_a_human():
    ev = s3()
    hyps = _ids(analysis.deterministic_triage(ev, NET_ACTIONS))
    result = analysis.verify(hyps, ev)
    rec = analysis.choose_action(hyps, result, NET_ACTIONS, ev)
    body = render.analysis(ev, {"mode": "rules", "reason": "test", "hypotheses": hyps}, result, None,
                           ev[7]["data"]["flags"], recommendation=rec)
    assert "### No action proposed" in body and "Recommended (for a human, through the pipeline)" in body
    assert "gh workflow run apply.yml" in body and "/approve" not in body
