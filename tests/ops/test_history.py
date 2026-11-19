"""G3 (19/11/2026): too little history never confirms, and every change in the window is
a hypothesis on both triage paths. The regression case is issue #7 (18/11/2026 21:06
UTC): two cold-start requests right after a catalog-api deploy fired api-latency; the
checks confirmed the vendor against an *expected* baseline with no history, and the
model filed the deploy (2 min before) under `lambda`, which has no check."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from incident import analysis, evidence, render

from .fixtures import NET_ACTIONS, s1

EXPECTED = "expected p95 (only 0 min of history before the alarm)"


def issue7():
    """Issue #7's evidence, with the numbers it had (E2-E5, E9) plus the sample counts
    the new evidence step records: 2 requests in the last 3 min, 1-2 calls each."""
    ev = s1()
    ev[0]["data"].update(name="sr-acme-tv-dev-api-latency", alarm_kind="api-latency")
    ev[1]["data"] = {"now": 2170.0, "baseline": None, "baseline_source": None, "points": 2, "history_min": 0,
                     "samples_now": 2, "samples_baseline": 0}
    for i, (dep, now, exp) in ((2, ("valkey", 2.2, 5.0)), (3, ("dynamodb", 53.3, 25.0)),
                               (4, ("vendor-recs", 1964.5, 250.0))):
        ev[i]["data"] = {"dependency": dep, "now": now, "baseline": exp, "baseline_source": EXPECTED, "points": 2,
                         "history_min": 0, "samples_now": 2 if dep != "vendor-recs" else 1, "samples_baseline": 0,
                         "errors_5m": 0}
    ev[8]["data"] = {"versions": [{"version": "4", "at": "21:04:46", "minutes_before_alarm": 1.6}], "live": "4"}
    ev.append({"id": "E10", "kind": "infra_changes", "summary": "lambda:PublishVersion x3, ...", "source": "test",
               "data": {"counts": {"lambda:PublishVersion20150331": 3}, "applies": []}})
    return ev


# What the model answered on #7 (Nova Lite, 18/11/2026), shortened.
MODEL_7 = [
    {"component": "vendor-recs", "statement": "vendor p95 rose from 250 ms to 1,964 ms", "impact": "slow recs",
     "cites": ["E5"], "action": "vendor-fallback-on"},
    {"component": "lambda", "statement": "catalog-api v4, published 2 min before, may have regressed",
     "impact": "all routes", "cites": ["E9", "E10"], "action": None},
    {"component": "unknown", "statement": "something unmonitored", "impact": "?", "cites": ["E1"], "action": None},
]


def _verdicts(hyps, ev):
    res = analysis.verify(hyps, ev)
    return {h["component"]: v["verdict"] for h, v in zip(hyps, res["verdicts"])}, res


def test_issue_7_rules_path_names_the_deploy_and_confirms_nothing_on_two_requests():
    ev = issue7()
    hyps = analysis.number(analysis.with_change_hypothesis(analysis.deterministic_triage(ev, NET_ACTIONS), ev, NET_ACTIONS))
    assert [h["component"] for h in hyps] == ["config-change"]
    assert "E9" in hyps[0]["cites"] and "catalog-api v4 2 min before" in hyps[0]["statement"]
    verdicts, res = _verdicts(hyps, ev)
    assert verdicts == {"config-change": "confirmed"}  # live code v4 published 2 min before the alarm
    also = {a["component"]: a["verdict"] for a in res["also_checked"]}
    assert also["vendor-recs"] == "not-enough-history"
    vendor = next(a for a in res["also_checked"] if a["component"] == "vendor-recs")
    assert "not enough history: 1 calls in the last 3 min (needs 10)" in vendor["checks"][0]["detail"]
    # no AppConfig rollback for a code deploy; nothing else is confirmed: no action
    assert analysis.choose_action(hyps, res, NET_ACTIONS, ev) is None


def test_issue_7_model_path_the_deploy_is_a_checked_hypothesis_and_the_vendor_is_not_confirmed():
    ev = issue7()
    hyps = analysis.sanitize_hypotheses(copy.deepcopy(MODEL_7), ev, NET_ACTIONS)
    assert [h["component"] for h in hyps] == ["vendor-recs", "lambda"]  # `unknown` dropped (model may not use it)
    hyps = analysis.number(analysis.with_change_hypothesis(hyps, ev, NET_ACTIONS))
    assert [h["component"] for h in hyps] == ["vendor-recs", "config-change"]  # the deploy's `lambda` lead is the change
    assert hyps[1]["statement"].startswith("catalog-api v4")  # the model's words are kept
    verdicts, res = _verdicts(hyps, ev)
    assert verdicts == {"vendor-recs": "not-enough-history", "config-change": "confirmed"}
    # the verifier model can't turn "not enough history" into "confirmed"
    res = analysis.merge_model_verdicts(res, [{"hypothesis": "H1", "verdict": "confirmed", "explanation": "x"}])
    assert res["verdicts"][0]["verdict"] == "not-enough-history"
    assert analysis.choose_action(hyps, res, NET_ACTIONS, ev) is None  # vendor-fallback-on is NOT proposed


def test_a_model_that_ignores_the_deploy_still_gets_the_change_hypothesis():
    ev = issue7()
    hyps = analysis.sanitize_hypotheses(copy.deepcopy(MODEL_7[:1]), ev, NET_ACTIONS)
    hyps = analysis.with_change_hypothesis(hyps, ev, NET_ACTIONS)
    assert [h["component"] for h in hyps] == ["vendor-recs", "config-change"]
    full = [dict(MODEL_7[0], component=c, cites=["E5"]) for c in ("vendor-recs", "valkey", "dynamodb")]
    assert len(analysis.with_change_hypothesis(full, ev, NET_ACTIONS)) == 4  # on top of triage's 3, never crowded out
    assert analysis.with_change_hypothesis(full, s1(), NET_ACTIONS) == full  # no change: nothing added


def test_enough_samples_but_no_history_still_is_not_enough():
    ev = issue7()
    ev[4]["data"]["samples_now"] = 60  # busy, but the baseline is only the documented expected p95
    assert analysis.verdict_from(analysis.check_dependency("vendor-recs", ev)) == "not-enough-history"
    ev[4]["data"].update(baseline=95.0, baseline_source="median of 9 min before the alarm", history_min=9)
    assert analysis.verdict_from(analysis.check_dependency("vendor-recs", ev)) == "confirmed"  # S1's real shape
    ev[4]["data"]["samples_now"] = 4  # history, but 4 calls now
    assert analysis.verdict_from(analysis.check_dependency("vendor-recs", ev)) == "not-enough-history"


def test_errors_are_counted_calls_and_still_decide():
    ev = issue7()
    ev[2]["data"].update(errors_5m=8, samples_now=0)  # S3: the breaker skips the cache, every skip an error
    assert analysis.verdict_from(analysis.check_dependency("valkey", ev)) == "confirmed"


def test_a_terraform_apply_in_the_window_is_a_hypothesis_and_confirms():
    ev = s1()
    ev.append({"id": "E10", "kind": "infra_changes", "summary": "Terraform applies (CI): apply-ops-1", "source": "test",
               "data": {"counts": {}, "applies": [{"session": "apply-ops-1", "first": "22:10:00", "last": "22:12:00",
                                                   "calls": 14, "minutes_before_alarm": 6}]}})
    hyps = analysis.number(analysis.deterministic_triage(ev, NET_ACTIONS))
    assert [h["component"] for h in hyps] == ["vendor-recs", "config-change"] and hyps[1]["cites"] == ["E10"]
    verdicts, res = _verdicts(hyps, ev)
    assert verdicts["config-change"] == "confirmed" and "Terraform apply apply-ops-1" in res["verdicts"][1]["checks"][0]["detail"]


def test_render_says_not_enough_history():
    ev = issue7()
    hyps = analysis.number(analysis.with_change_hypothesis(analysis.sanitize_hypotheses(copy.deepcopy(MODEL_7), ev, NET_ACTIONS), ev, NET_ACTIONS))
    _, res = _verdicts(hyps, ev)
    body = render.analysis(ev, {"mode": "model", "model": "m", "input_tokens": 1, "output_tokens": 1,
                                "hypotheses": hyps}, res, None, {})
    assert "**H1 `vendor-recs`**: **not enough history**" in body and "**H2 `config-change`**: **CONFIRMED**" in body


# ------------------------------------------------------------------ evidence side
def test_split_series_counts_samples_and_history():
    t0 = datetime(2026, 11, 18, 21, 0, tzinfo=timezone.utc)
    times = [t0 + timedelta(minutes=i) for i in range(6)]
    s = evidence.split_series([90, 95, 100, 1600, 1650, 1700], times, t0 + timedelta(minutes=3),
                              counts={t: 12 for t in times})
    assert (s["history_min"], s["samples_now"], s["samples_baseline"]) == (3, 36, 36)
    two = evidence.split_series([1332, 3007], times[4:], t0 + timedelta(minutes=2), expected=250.0,
                                counts={times[4]: 1, times[5]: 1})
    assert (two["history_min"], two["samples_now"], two["baseline_source"]) == (0, 2, EXPECTED)


class Trail:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def lookup_events(self, **kw):
        page = self.pages[self.calls]
        self.calls += 1
        return page


def test_cloudtrail_finds_terraform_applies_by_the_ci_session_and_pages():
    end = datetime(2026, 11, 18, 21, 20, tzinfo=timezone.utc)
    ev = lambda user, minute, name="PutRolePolicy", src="iam.amazonaws.com": {  # noqa: E731
        "EventSource": src, "EventName": name, "Username": user, "EventTime": end - timedelta(minutes=minute)}
    noise = [ev("i-0abc", 1, "UpdateInstanceInformation", "ssm.amazonaws.com")] * 50
    trail = Trail([{"Events": noise, "NextToken": "t1"},
                   {"Events": [ev("apply-ops-77", 9), ev("apply-ops-77", 7), ev("sr-ops-evidence", 2)]}])
    c = evidence.Collector(evidence.Clients(None, None, None, trail), "acme-tv", "dev", {}, {})
    c.alarm_at = end
    c.cloudtrail(end)
    item = c.items[-1]
    assert trail.calls == 2
    assert item["data"]["applies"] == [{"session": "apply-ops-77", "first": "21:11:00", "last": "21:13:00",
                                        "calls": 2, "minutes_before_alarm": 7.0}]
    assert "Terraform applies (CI): apply-ops-77" in item["summary"] and "newest 53" in item["summary"]
