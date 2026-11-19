import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "sr"))
import preflight  # noqa: E402
from preflight import FAIL, PASS, WARN  # noqa: E402


def test_alarms():
    a = [{"AlarmName": "sr-acme-tv-dev-api-latency", "StateValue": "OK"},
         {"AlarmName": "sr-acme-tv-dev-vendor-latency", "StateValue": "INSUFFICIENT_DATA"},
         {"AlarmName": "sr-acme-tv-prod-api-latency", "StateValue": "ALARM"}]  # other env: ignored
    r = preflight.eval_alarms(a, "dev")
    assert r.status == PASS and "1 INSUFFICIENT_DATA" in r.detail
    a[0]["StateValue"] = "ALARM"
    assert preflight.eval_alarms(a, "dev").status == FAIL
    assert preflight.eval_alarms([], "dev").status == FAIL


def test_runtime_config_must_be_at_defaults():
    flags = {"vendor_fallback": False, "catalog_ttl_s": 300, "recs_ttl_s": 120}
    chaos = {"vendor_latency_ms": 0, "vendor_error_rate": 0}
    assert preflight.eval_runtime_config("acme-tv", flags, chaos).status == PASS
    r = preflight.eval_runtime_config("acme-tv", flags, {**chaos, "vendor_latency_ms": 1500})
    assert r.status == FAIL and "vendor_latency_ms" in r.detail and "demo reset" in r.detail


def test_budgets():
    def b(name, limit, spent, forecast=0):
        return {"BudgetName": name, "BudgetLimit": {"Amount": str(limit)},
                "CalculatedSpend": {"ActualSpend": {"Amount": str(spent)}, "ForecastedSpend": {"Amount": str(forecast)}}}
    got = [r.status for r in preflight.eval_budgets([b("m", 60, 3), b("m", 60, 10, 50), b("br", 5, 5)])]
    assert got == [PASS, WARN, FAIL]
    assert preflight.eval_budgets([])[0].status == FAIL


def test_quota_and_nat():
    assert preflight.eval_quota(10).status == WARN and preflight.eval_quota(1000).status == PASS
    inst = [{"InstanceId": "i-1", "LifecycleState": "InService", "HealthStatus": "Healthy"}]
    ok = [{"DestinationCidrBlock": "0.0.0.0/0", "InstanceId": "i-1", "State": "active"}]
    assert preflight.eval_nat(inst, ok).status == PASS
    stale = [{"DestinationCidrBlock": "0.0.0.0/0", "InstanceId": "i-0", "State": "blackhole"}]
    assert preflight.eval_nat(inst, stale).status == FAIL       # replaced instance, route not healed
    assert preflight.eval_nat([], ok).status == FAIL
    assert preflight.eval_nat(inst, []).status == FAIL


def test_nat_ami_pin_warns_when_aws_publishes_a_newer_one():
    assert preflight.eval_nat_ami("ami-0a", "ami-0a").status == PASS
    r = preflight.eval_nat_ami("ami-0a", "ami-0b")
    assert r.status == WARN and "not on a demo day" in r.detail


def test_the_smoke_test_budget_is_not_a_spending_signal():
    smoke = {"BudgetName": "showrunner-smoke-test", "BudgetLimit": {"Amount": "1"},
             "CalculatedSpend": {"ActualSpend": {"Amount": "1.11"}}}
    real = {"BudgetName": "showrunner-monthly", "BudgetLimit": {"Amount": "60"},
            "CalculatedSpend": {"ActualSpend": {"Amount": "1.11"}}}
    got = preflight.eval_budgets([smoke, real])
    assert [r.name for r in got] == ["budget showrunner-monthly"] and got[0].status == PASS


def test_a_crashing_check_is_a_fail_not_a_crash(monkeypatch, capsys):
    def boom(env):
        raise RuntimeError("nope")
    monkeypatch.setattr(preflight, "CHECKS", [boom, lambda env: [preflight.Result(PASS, "x", "fine")]])
    assert preflight.run("dev") == 1
    out = capsys.readouterr().out
    assert "FAIL  boom" in out and "PASS  x" in out and "NOT READY: 1 fail" in out


def test_network_rule_must_be_in_place():
    ok = {"drift": False, "missing_ports": [], "cache_sg": "sg-c"}
    assert preflight.eval_network(ok).status == PASS
    r = preflight.eval_network({"drift": True, "missing_ports": [6379, 6380], "cache_sg": "sg-c"})
    assert r.status == FAIL and "s3 stop" in r.detail
