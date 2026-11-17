import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ci"))

import changed_stacks as cs  # noqa: E402


def make_repo(tmp_path: Path) -> dict:
    p = tmp_path / "platform"
    for d in ("bootstrap", "shared", "envs", "ops-core", "customers/acme-tv", "customers/nordic-sports"):
        (p / d).mkdir(parents=True)
    for f in ("acme-tv/dev.yaml", "acme-tv/prod.yaml", "nordic-sports/prod.yaml"):
        (p / "customers" / f).write_text("customer: x\n")
    return cs.discover(tmp_path)


def keys(result):
    return [s["stack"] for s in result]


def test_discovers_all_stacks(tmp_path):
    stacks = make_repo(tmp_path)
    assert set(stacks) == {
        "bootstrap", "ops-core", "shared/dev", "shared/prod",
        "customers/acme-tv/dev", "customers/acme-tv/prod", "customers/nordic-sports/prod",
    }


def test_one_customer_yaml_plans_one_stack(tmp_path):
    stacks = make_repo(tmp_path)
    assert keys(cs.affected(["platform/customers/acme-tv/dev.yaml"], stacks)) == ["customers/acme-tv/dev"]


def test_module_change_plans_every_consumer(tmp_path):
    stacks = make_repo(tmp_path)
    got = keys(cs.affected(["platform/modules/streaming-service/main.tf"], stacks))
    assert got == ["customers/acme-tv/dev", "customers/acme-tv/prod", "customers/nordic-sports/prod"]


def test_network_change_plans_shared_only(tmp_path):
    stacks = make_repo(tmp_path)
    assert keys(cs.affected(["platform/modules/network/nat.tf"], stacks)) == ["shared/dev", "shared/prod"]


def test_policy_change_plans_everything(tmp_path):
    stacks = make_repo(tmp_path)
    assert len(cs.affected(["platform/policy/never_create.rego"], stacks)) == len(stacks)


def test_docs_change_plans_nothing(tmp_path):
    stacks = make_repo(tmp_path)
    assert cs.affected(["README.md", "docs/COST.md"], stacks) == []


def test_unknown_customer_yaml_ignored(tmp_path):
    stacks = make_repo(tmp_path)
    assert cs.affected(["platform/customers/ghost/dev.yaml"], stacks) == []


def test_bootstrap_only_repo(tmp_path):
    (tmp_path / "platform" / "bootstrap").mkdir(parents=True)
    stacks = cs.discover(tmp_path)
    assert keys(cs.affected(["platform/bootstrap/roles.tf"], stacks)) == ["bootstrap"]


def test_lambda_module_plans_shared_and_customers(tmp_path):
    stacks = make_repo(tmp_path)
    got = keys(cs.affected(["platform/modules/lambda-function/main.tf"], stacks))
    assert got == ["customers/acme-tv/dev", "customers/acme-tv/prod", "customers/nordic-sports/prod", "shared/dev", "shared/prod"]


def test_vendor_mock_module_plans_shared(tmp_path):
    stacks = make_repo(tmp_path)
    assert keys(cs.affected(["platform/modules/vendor-mock/main.tf"], stacks)) == ["shared/dev", "shared/prod"]


def test_service_code_plans_nothing(tmp_path):
    """App code is lane 2 (tools/deploy), not Terraform."""
    stacks = make_repo(tmp_path)
    assert cs.affected(["services/catalog-api/catalog_api/handler.py"], stacks) == []


def test_plan_comment_summary():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ci"))
    import plan_comment

    body = plan_comment.summarise("customers/acme-tv/dev", "Plan: 1 to add, 2 to change, 0 to destroy.", True)
    assert "1 to add, 2 to change, **0 to destroy**" in body and "passed" in body
    assert "no changes" in plan_comment.summarise("x", "No changes. Your infrastructure matches", True)
    assert len(plan_comment.summarise("x", "a" * 100000, False)) < 61000
