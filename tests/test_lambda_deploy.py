"""Lane 2 through the canary (tools/deploy/lambda_deploy.py, ADR 0016), against a fake
AWS CLI that keeps Lambda versions, alias `live` with its routing weights, and alarms."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "deploy"))
import lambda_deploy as ld  # noqa: E402

FN = "sr-acme-tv-dev-catalog-api"
ALARMS = ["sr-acme-tv-dev-canary-errors", "sr-acme-tv-dev-api-latency"]
CANARY = ld.Canary(weight=0.1, minutes=5, alarms=ALARMS)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeAws:
    def __init__(self, live_code: bytes, clock: Clock, fire_at: float | None = None, fire: str = ALARMS[0]):
        self.versions = {"4": ld.code_sha256(live_code)}
        self.code = {"4": live_code}
        self.alias, self.weights = "4", {}
        self.latest_sha = self.versions["4"]
        self.clock, self.fire_at, self.fire = clock, fire_at, fire
        self.calls: list[list[str]] = []
        self.history: list[tuple[str, dict]] = []  # every alias state, in order

    def __call__(self, args: list[str]) -> dict:
        self.calls.append(args)
        op = tuple(args[:2])
        arg = dict(zip(args[2::2], args[3::2]))
        if op == ("lambda", "get-alias"):
            return {"FunctionVersion": self.alias}
        if op == ("lambda", "get-function-configuration"):
            return {"CodeSha256": self.versions[arg["--qualifier"]]}
        if op == ("lambda", "get-function"):
            v = arg["--qualifier"]
            return {"Configuration": {"CodeSha256": self.versions[v]}, "Code": {"Location": f"https://s3/{v}"}}
        if op == ("lambda", "update-function-code"):
            self.latest_sha = ld.code_sha256(Path(arg["--zip-file"].removeprefix("fileb://")).read_bytes())
            return {}
        if op == ("lambda", "wait"):
            return {}
        if op == ("lambda", "publish-version"):
            if arg["--code-sha256"] != self.latest_sha:
                raise ld.AwsError("CodeSha256 does not match")  # what Lambda does
            v = str(max(map(int, self.versions)) + 1)
            self.versions[v] = self.latest_sha
            return {"Version": v}
        if op == ("lambda", "update-alias"):
            self.alias = arg["--function-version"]
            self.weights = json.loads(arg["--routing-config"])["AdditionalVersionWeights"]
            self.history.append((self.alias, dict(self.weights)))
            return {}
        if op == ("cloudwatch", "describe-alarms"):
            hot = self.fire_at is not None and self.clock.now() >= self.fire_at
            return {"MetricAlarms": [{"AlarmName": n, "StateValue": "ALARM" if hot and n == self.fire else "OK"}
                                     for n in args[args.index("--alarm-names") + 1:]]}
        if op == ("ssm", "get-parameters"):
            names = args[args.index("--names") + 1:]
            vals = {"function_name": FN, "canary": json.dumps(CANARY.__dict__)}
            return {"Parameters": [{"Name": n, "Value": vals[n.rsplit("/", 1)[1]]} for n in names]}
        raise AssertionError(f"unexpected call {args}")


@pytest.fixture
def zips(tmp_path):
    old, new = tmp_path / "old.zip", tmp_path / "new.zip"
    old.write_bytes(b"PK old build")
    new.write_bytes(b"PK new build")
    return old, new


def run(fake, zip_path, **kw):
    out: list[str] = []
    rs = ld.rollout([(FN, CANARY)], zip_path, call=fake, sleep=fake.clock.sleep, now=fake.clock.now,
                    out=out.append, fetch=lambda url: fake.code[url.rsplit("/", 1)[1]], **kw)
    return rs[0], out


def test_a_good_build_gets_10_percent_for_5_minutes_then_everything(zips):
    old, new = zips
    fake = FakeAws(old.read_bytes(), Clock())
    r, out = run(fake, new)
    assert (r.status, r.current, r.target) == ("promoted", "4", "5")
    assert fake.history == [("4", {"5": 0.1}), ("5", {})]  # split, then all on v5
    publish = next(c for c in fake.calls if c[:2] == ["lambda", "publish-version"])
    assert publish[publish.index("--code-sha256") + 1] == ld.code_sha256(new.read_bytes())
    alarm_checks = [c for c in fake.calls if c[0] == "cloudwatch"]
    assert len(alarm_checks) >= 5 * 60 / ld.POLL_S  # watched the whole window
    assert r.restored is None and ld.summary([r])["ok"] is True


def test_a_bad_build_is_rolled_back_and_the_live_code_becomes_the_newest_version(zips):
    """S2: the canary alarm fires 2 minutes in; live goes back to v4 only. Then v4's code
    is re-published as v6, so Terraform (which points live at the newest version) can
    never bring v5 back."""
    old, new = zips
    clock = Clock()
    fake = FakeAws(old.read_bytes(), clock, fire_at=clock.t + 120)
    r, out = run(fake, new)
    assert (r.status, fake.alias, fake.weights, r.target, r.restored) == ("rolled-back", "4", {}, "5", "6")
    assert fake.history == [("4", {"5": 0.1}), ("4", {})]  # v5 never got more than 10%
    assert fake.versions["6"] == fake.versions["4"] != fake.versions["5"]
    assert "sr-acme-tv-dev-canary-errors" in r.detail
    assert ld.summary([r])["ok"] is False
    assert any("back on v4" in line for line in out)


def test_no_deploy_starts_while_a_rollback_alarm_is_firing(zips):
    old, new = zips
    clock = Clock()
    fake = FakeAws(old.read_bytes(), clock, fire_at=0, fire=ALARMS[1])
    r, _ = run(fake, new)
    assert r.status == "refused" and "api-latency" in r.detail
    assert not any(c[1] in {"update-function-code", "publish-version", "update-alias"} for c in fake.calls)


def test_the_same_code_twice_is_a_no_op(zips):
    old, _ = zips
    fake = FakeAws(old.read_bytes(), Clock())
    r, _ = run(fake, old)
    assert r.status == "already-live"
    assert not any(c[1] in {"update-function-code", "publish-version", "update-alias"} for c in fake.calls)
    assert ld.summary([r])["ok"] is True


def test_publish_fails_if_the_code_changed_under_us(zips):
    """Deploy by digest: if $LATEST isn't exactly the zip we uploaded (a concurrent
    update), PublishVersion with CodeSha256 refuses and the alias never moves."""
    old, new = zips
    fake = FakeAws(old.read_bytes(), Clock())
    real = fake.__call__

    def racing(args):
        res = real(args)
        if args[:2] == ["lambda", "update-function-code"]:
            fake.latest_sha = "someone-else"
        return res

    with pytest.raises(ld.AwsError, match="CodeSha256"):
        ld.rollout([(FN, CANARY)], new, call=racing, sleep=lambda s: None, out=lambda s: None)
    assert fake.history == [] and fake.alias == "4"


def test_an_error_mid_canary_puts_all_traffic_back(zips):
    old, new = zips
    fake = FakeAws(old.read_bytes(), Clock())
    real = fake.__call__
    seen = {"n": 0}

    def flaky(args):
        if args[0] == "cloudwatch":
            seen["n"] += 1
            if seen["n"] == 3:  # first two: the pre-check and one canary look
                raise ld.AwsError("Throttling")
        return real(args)

    with pytest.raises(ld.AwsError, match="Throttling"):
        ld.rollout([(FN, CANARY)], new, call=flaky, sleep=fake.clock.sleep, now=fake.clock.now, out=lambda s: None)
    assert fake.history == [("4", {"5": 0.1}), ("4", {})]


def test_all_at_once_skips_the_window_but_not_the_alarm_check(zips):
    old, new = zips
    fake = FakeAws(old.read_bytes(), Clock())
    r, _ = run(fake, new, all_at_once=True)
    assert r.status == "promoted" and fake.history == [("5", {})]
    assert any(c[0] == "cloudwatch" for c in fake.calls)


def test_main_reads_the_canary_config_terraform_published(zips):
    old, new = zips
    fake = FakeAws(old.read_bytes(), Clock())
    fn, canary = ld.customer_target("acme-tv", "dev", fake)
    assert fn == FN and canary == CANARY
    ssm = next(c for c in fake.calls if c[0] == "ssm")
    assert "/showrunner/dev/customers/acme-tv/canary" in ssm


def test_main_refuses_a_zip_that_is_not_the_attested_one(zips, capsys):
    _, new = zips
    assert ld.main(["--zip", str(new), "--env", "dev", "--customer", "acme-tv", "--expect-sha256", "not-it"]) == 2
    assert "refusing" in capsys.readouterr().err


def test_restore_refuses_downloaded_code_that_does_not_match(zips):
    old, _ = zips
    fake = FakeAws(old.read_bytes(), Clock())
    with pytest.raises(ld.AwsError, match="doesn't match"):
        ld.restore_latest(FN, fake, fetch=lambda url: b"tampered")


def test_the_terraform_canary_matches_the_tool_defaults():
    tf = (ROOT / "platform/modules/streaming-service/variables.tf").read_text()
    assert "weight         = optional(number, 0.1)" in tf and "minutes        = optional(number, 5)" in tf
    out = (ROOT / "platform/modules/streaming-service/outputs.tf").read_text()
    assert "canary" in out and "jsonencode(local.canary)" in out


def test_the_workflow_verifies_provenance_before_deploying_and_installs_nothing():
    wf = (ROOT / ".github/workflows/build-deploy.yml").read_text()
    deploy = wf[wf.index("\n  deploy:"):]
    assert deploy.index("gh attestation verify") < deploy.index("lambda_deploy.py")
    assert "--signer-workflow" in deploy and "--source-ref refs/heads/main" in deploy
    assert "--expect-sha256" in deploy
    for installer in ("pip install", "uv sync", "setup-uv", "npm "):
        assert installer not in deploy
    assert "showrunner-ci-deploy-dev" in deploy and "environment: dev" in deploy
    assert "group: apply-customers/${{ matrix.customer }}/dev" in deploy
