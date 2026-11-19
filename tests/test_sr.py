"""sr CLI: which AWS profile it picks, the account guard's messages, and the traffic
tool's stop/throttle reporting (no AWS calls)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/sr"))
import common  # noqa: E402
import traffic  # noqa: E402


@pytest.mark.parametrize("env,want", [
    ({}, "showrunner"),                                    # nothing set: never the default profile
    ({"AWS_PROFILE": "other"}, "other"),                   # an explicit choice wins
    ({"AWS_ACCESS_KEY_ID": "x"}, None),                    # CI / OIDC credentials in env
    ({"AWS_PROFILE": "p", "AWS_ACCESS_KEY_ID": "x"}, "p"),
])
def test_profile_name(env, want):
    assert common.profile_name(env) == want


class FakeSts:
    def __init__(self, account=None, exc=None):
        self.account, self.exc = account, exc

    def get_caller_identity(self):
        if self.exc:
            raise self.exc
        return {"Account": self.account}


def guard(monkeypatch, capsys, sts, env=None):
    common.check_account.cache_clear()
    monkeypatch.setattr(common, "client", lambda name: sts)
    monkeypatch.setenv("AWS_ACCOUNT_ID", "111111111111")
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        common.check_account()
    err = capsys.readouterr().err.strip()
    assert e.value.code == 2 and "\n" not in err  # one line, no traceback
    return err


def test_wrong_account_is_one_line(monkeypatch, capsys):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    err = guard(monkeypatch, capsys, FakeSts(account="222222222222"))
    assert "222222222222, not 111111111111" in err and "AWS_PROFILE=showrunner" in err


def test_invalid_token_is_one_line(monkeypatch, capsys):
    from botocore.exceptions import ClientError
    exc = ClientError({"Error": {"Code": "InvalidClientTokenId", "Message": "bad"}}, "GetCallerIdentity")
    err = guard(monkeypatch, capsys, FakeSts(exc=exc), {"AWS_PROFILE": "default"})
    assert err.startswith("sr: profile 'default' can't sign in to AWS (InvalidClientTokenId)")


def test_classify_separates_throttling():
    e = Exception()
    assert traffic.classify(None, 200) == "ok"
    assert traffic.classify(e, 429) == "throttled"
    assert traffic.classify(e, 503) == "5xx"
    assert traffic.classify(e, 404) == "4xx"
    assert traffic.classify(e, None) == "network"


def test_stop_message_always_says_why():
    msg = traffic.stop_message("--minutes 20 reached", __import__("time").time() - 1200, 2400,
                               {"ok": 2390, "throttled": 10, "5xx": 0})
    assert msg.startswith("traffic stopped: --minutes 20 reached after 20.0 min; 2400 requests sent")
    assert "throttled=10" in msg and "5xx" not in msg


# ---------------------------------------------------------------- undo (issue #6 follow-up)
import undo  # noqa: E402

ACT = {"profile": "flags", "before": {"vendor_fallback": False, "catalog_ttl_s": 300},
       "after": {"vendor_fallback": True, "catalog_ttl_s": 300}, "deployment": 14}


def row(sk, step, **kw):
    return {"pk": "acme-tv-dev-20261118T1010", "sk": f"{sk}#{step}", "step": step, **kw}


def test_only_changed_keys_count():
    assert undo.changed_keys(ACT) == {"vendor_fallback": (False, True)}
    assert undo.is_undone(ACT, {"vendor_fallback": False, "catalog_ttl_s": 999})
    assert not undo.is_undone(ACT, {"vendor_fallback": True, "catalog_ttl_s": 300})


def test_open_mitigation_is_the_last_act_without_a_later_undo():
    rows = [row("2026-11-18T10:10:07", "open"),
            row("2026-11-18T10:12:34", "act", approval_comment="https://github.com/o/r/issues/6#issuecomment-1"),
            row("2026-11-18T10:15:37", "pir")]
    assert undo.open_mitigation(rows)["step"] == "act"
    assert undo.issue_of(rows) == ("o/r", 6)
    assert undo.open_mitigation(rows + [row("2026-11-18T10:21:40", "undo")]) is None
    assert undo.open_mitigation(rows[:1]) is None


def test_comment_names_the_deployment_and_answers_the_follow_up():
    body = undo.comment_body("flags", ACT, 16, {"vendor_fallback": False}, "Anshul-exe", "S1 stop: back to defaults",
                             "2026-11-18 10:21:40", "2026-11-18 10:21:52")
    assert body.startswith("**Mitigation undone**: `flags` back to defaults (`vendor_fallback: true -> false`), "
                           "AppConfig deployment #16, at 2026-11-18 10:21:40 UTC by Anshul-exe")
    assert "recorded" not in body.split("\n")[0]          # same minute: no "recorded later" note
    late = undo.comment_body("flags", ACT, 16, {"vendor_fallback": False}, "x", "y", "2026-11-18 10:21:40", "2026-11-18 16:40:00")
    assert "(recorded 2026-11-18 16:40:00 UTC)" in late
    assert "first follow-up" in body


def test_s4_typo_host_is_nxdomain_shaped_and_refuses_other_urls():
    import scenarios
    url = "https://abcdef.lambda-url.us-east-1.on.aws/"
    assert scenarios.typo_host(url) == "https://abcdef.lambda-ur.us-east-1.on.aws"
    with pytest.raises(ValueError):
        scenarios.typo_host("https://example.com")


def test_s3_restores_the_rule_exactly_as_terraform_declares_it():
    """`stop` must leave no drift: same ports and description as modules/cache."""
    import re
    import scenarios
    tf = (Path(__file__).resolve().parents[1] / "platform" / "modules" / "cache" / "main.tf").read_text()
    ingress = re.search(r"ingress \{(.*?)\}", tf, re.S).group(1)
    assert f'"{scenarios.S3_DESCRIPTION}"' in ingress
    assert re.search(r"from_port\s*=\s*6379", ingress) and re.search(r"to_port\s*=\s*6380", ingress)
    assert scenarios.S3_PORTS == (6379, 6380)
