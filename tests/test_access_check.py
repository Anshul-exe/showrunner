"""tools/ci/access_check.py: the PR-time check that the apply role can read what the
plan read. Fixture = the shape of ops-core's real plan on 18/11/2026."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/ci"))
import access_check as ac  # noqa: E402

ACCOUNT = "123456789012"
ARN = f"arn:aws:ssm:us-east-1:{ACCOUNT}:parameter"


def plan():
    def param(addr, name):
        return {"address": addr, "mode": "data", "type": "aws_ssm_parameter",
                "values": {"name": name, "arn": f"{ARN}{name}", "value": "SECRET-IN-PLAN-JSON"}}
    return {"prior_state": {"values": {"root_module": {
        "resources": [param("data.aws_ssm_parameter.workload_boundary_arn", "/showrunner/bootstrap/workload_boundary_arn")],
        "child_modules": [{"resources": [
            param('module.ops_core.data.aws_ssm_parameter.strategy["dev"]', "/showrunner/dev/shared/appconfig_strategy_id"),
            {"address": 'module.ops_core.data.aws_ssm_parameters_by_path.customers["dev"]', "mode": "data",
             "type": "aws_ssm_parameters_by_path", "values": {"path": "/showrunner/dev/customers"}},
            {"address": "module.ops_core.aws_dynamodb_table.audit", "mode": "managed", "type": "aws_dynamodb_table",
             "values": {}},
        ]}],
    }}}}


class FakeSsm:
    def list_tags_for_resource(self, ResourceType, ResourceId):
        env = ResourceId.split("/")[2]  # /showrunner/<env>/...
        return {"TagList": [{"Key": "env", "Value": env}]}


class FakeIam:
    """Mimics the 18/11 prod apply role: deny anything tagged env=dev, unless allowed."""

    def __init__(self, carve_out=()):
        self.calls, self.carve_out = [], set(carve_out)

    def simulate_principal_policy(self, PolicySourceArn, ActionNames, ResourceArns, ContextEntries):
        self.calls.append((ActionNames[0], ResourceArns[0], ContextEntries))
        tags = {c["ContextKeyName"]: c["ContextKeyValues"][0] for c in ContextEntries}
        denied = tags.get("aws:ResourceTag/env") == "dev" and ResourceArns[0] not in self.carve_out
        return {"EvaluationResults": [{"EvalDecision": "explicitDeny" if denied else "allowed"}]}


def test_reads_found_in_root_and_child_modules():
    reads = ac.ssm_reads(plan(), "us-east-1", ACCOUNT)
    assert [(r.action, r.arn) for r in reads] == [
        ("ssm:GetParameter", f"{ARN}/showrunner/bootstrap/workload_boundary_arn"),
        ("ssm:GetParameter", f"{ARN}/showrunner/dev/shared/appconfig_strategy_id"),
        ("ssm:GetParametersByPath", f"{ARN}/showrunner/dev/customers"),
    ]


def test_the_18_11_failure_is_caught_with_tag_context():
    iam = FakeIam()
    results = ac.check(ac.ssm_reads(plan(), "us-east-1", ACCOUNT), "arn:role", iam, FakeSsm())
    out = ac.render("showrunner-ci-apply-prod", results)
    assert "**FAILED**" in out and "appconfig_strategy_id" in out and "explicitDeny" in out
    # the tag the deny keys on was passed to the simulator; paths have no tags
    by_arn = {arn: ctx for _, arn, ctx in iam.calls}
    assert by_arn[f"{ARN}/showrunner/dev/shared/appconfig_strategy_id"][0]["ContextKeyName"] == "aws:ResourceTag/env"
    assert by_arn[f"{ARN}/showrunner/dev/customers"] == []


def test_passes_after_the_carve_out_and_never_prints_values():
    iam = FakeIam(carve_out={f"{ARN}/showrunner/dev/shared/appconfig_strategy_id"})
    out = ac.render("r", ac.check(ac.ssm_reads(plan(), "us-east-1", ACCOUNT), "arn:role", iam, FakeSsm()))
    assert out.startswith("Apply-role access (`r`): passed (3 SSM reads allowed)")
    assert "SECRET-IN-PLAN-JSON" not in out


@pytest.mark.parametrize("stack,role", [
    ("ops-core", "showrunner-ci-apply-prod"),
    ("shared/dev", "showrunner-ci-apply-dev"),
    ("customers/acme-tv/dev", "showrunner-ci-apply-dev"),
    ("customers/acme-tv/prod", "showrunner-ci-apply-prod"),
])
def test_apply_role_per_stack(stack, role):
    assert ac.apply_role(stack) == role


def test_apply_role_matches_apply_workflow():
    wf = (Path(__file__).resolve().parents[1] / ".github/workflows/apply.yml").read_text()
    assert "role/showrunner-ci-apply-dev" in wf and "role/showrunner-ci-apply-prod" in wf
