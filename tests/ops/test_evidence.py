import io
from datetime import datetime, timedelta, timezone

from incident import evidence

END = datetime(2026, 11, 17, 22, 20, tzinfo=timezone.utc)
PARAMS = {"api_url": "https://abc123.execute-api.us-east-1.amazonaws.com", "function_name": "sr-acme-tv-dev-catalog-api",
          "appconfig_application_id": "app", "appconfig_environment_id": "env",
          "flags_profile_id": "flags", "chaos_profile_id": "chaos"}
SHARED = {"cache_name": "sr-dev-cache", "appconfig_strategy_id": "strat"}


def test_split_series_now_vs_baseline():
    s = evidence.split_series([100, 110, 90, 1600, 1650, 1700])
    assert (s["now"], s["baseline"], s["points"]) == (1650, 100, 6)
    assert evidence.split_series([1600, 1650])["baseline"] is None
    assert evidence.split_series([])["now"] is None


def test_baseline_never_includes_the_minutes_of_the_problem():
    # The real run on 17/11/2026 23:11 UTC: traffic started ~1.5 min before the chaos, so
    # the only "before" minute was the first one, inflated by cold starts (both
    # customers' vendor p95 ~1.6 s, including the one without chaos).
    alarm = datetime(2026, 11, 17, 23, 11, 5, tzinfo=timezone.utc)
    times = [alarm - timedelta(minutes=m) for m in (4, 3, 2, 1)]
    s = evidence.split_series([1366, 1600, 1692, 1700], times, alarm - evidence.ONSET_LEAD,
                              expected=evidence.EXPECTED_P95_MS["vendor-recs"])
    assert s["baseline"] == 250.0 and s["baseline_source"].startswith("expected p95 (only 0 min")
    times = [alarm - timedelta(minutes=m) for m in range(12, 0, -1)]
    vals = [95, 2400, 90, 92, 96, 94, 93, 95, 1580, 1600, 1620, 1650]  # one cold-start minute
    s = evidence.split_series(vals, times, alarm - evidence.ONSET_LEAD, expected=250.0)
    assert s["baseline"] == 94.5 and s["baseline_source"] == "median of 8 min before the alarm"
    assert s["now"] == 1620


class CW:
    def get_metric_data(self, MetricDataQueries, **kw):
        vals = {"api": [110, 105, 1600, 1650, 1700], "lat_vendor_recs": [95, 90, 1580, 1600, 1620],
                "lat_valkey": [1.5, 1.4, 1.6, 1.5, 1.6], "lat_dynamodb": [9, 9, 9, 9, 9],
                "read": [1100, 1180], "write": [1900]}
        return {"MetricDataResults": [{"Id": q["Id"], "Values": vals.get(q["Id"], [])} for q in MetricDataQueries]}


class AppConfig:
    def list_deployments(self, **kw):
        return {"Items": [
            {"DeploymentNumber": 7, "State": "COMPLETE", "StartedAt": END - timedelta(minutes=3)},   # chaos
            {"DeploymentNumber": 5, "State": "COMPLETE", "StartedAt": END - timedelta(hours=5)}]}    # old flags

    def get_deployment(self, DeploymentNumber, **kw):
        return {"ConfigurationProfileId": "chaos" if DeploymentNumber == 7 else "flags",
                "ConfigurationVersion": "3", "Description": "x"}

    def get_hosted_configuration_version(self, **kw):
        return {"Content": io.BytesIO(b'{"vendor_fallback": false}')}


class Lambda:
    def get_paginator(self, name):
        class P:
            def paginate(self, **kw):
                return [{"Versions": [{"Version": "$LATEST", "LastModified": "2026-11-17T22:00:00.000+0000"},
                                      {"Version": "3", "LastModified": "2026-11-17T18:00:00.000+0000"}]}]
        return P()

    def get_alias(self, **kw):
        return {"FunctionVersion": "3"}


class Trail:
    def lookup_events(self, **kw):
        return {"Events": [{"EventSource": "appconfig.amazonaws.com", "EventName": "StartDeployment", "Username": "x"},
                           {"EventSource": "ec2.amazonaws.com", "EventName": "ReplaceRoute", "Username": "i-1"},
                           {"EventSource": "dynamodb.amazonaws.com", "EventName": "PutItem", "Username": "sr-ops-incident-github"}]}


def test_collect_s1_shape_and_hides_the_simulated_vendor():
    alarm = {"name": "sr-acme-tv-dev-vendor-latency", "customer": "acme-tv", "env": "dev", "kind": "vendor-latency",
             "time": "2026-11-17 22:20:09", "reason": "Threshold crossed"}
    items = evidence.collect(evidence.Clients(CW(), AppConfig(), Lambda(), Trail()), alarm, PARAMS, SHARED, END)
    kinds = [i["kind"] for i in items]
    assert kinds == ["alarm", "api_latency", "dependency_latency", "dependency_latency", "dependency_latency",
                     "valkey_server_latency", "config_changes", "runtime_flags", "deploys", "infra_changes"]
    assert [i["id"] for i in items] == [f"E{n}" for n in range(1, 11)]
    vendor = next(i for i in items if i["kind"] == "dependency_latency" and i["data"]["dependency"] == "vendor-recs")
    assert vendor["data"]["now"] == 1600 and vendor["data"]["baseline"] == 250.0  # 2 points before: expected
    changes = next(i for i in items if i["kind"] == "config_changes")
    assert changes["data"]["changes"] == []                       # chaos deployment not shown, old flags out of window
    assert "excluded on purpose" in changes["summary"]
    infra = next(i for i in items if i["kind"] == "infra_changes")
    assert infra["data"]["counts"] == {"ec2:ReplaceRoute": 1}       # AppConfig and the loop's own calls excluded


def test_a_failing_source_is_recorded_not_fatal():
    class Broken(Trail):
        def lookup_events(self, **kw):
            raise RuntimeError("throttled")
    alarm = {"name": "n", "customer": "acme-tv", "env": "dev", "kind": "vendor-latency", "time": "t", "reason": "r"}
    items = evidence.collect(evidence.Clients(CW(), AppConfig(), Lambda(), Broken()), alarm, PARAMS, SHARED, END)
    assert items[-1]["kind"] == "collection_error" and "RuntimeError" in items[-1]["summary"]


class RecoveryCW:
    """API p95 and request counts per minute, oldest first, ending at END."""

    def __init__(self, p95s, counts, gap_until=None):
        self.p95s, self.counts = p95s, counts

    def get_metric_data(self, MetricDataQueries, **kw):
        n = len(self.counts)
        ts = [END - timedelta(minutes=n - i) for i in range(n)]
        p = [(t, v) for t, v in zip(ts, self.p95s) if v is not None]
        c = [(t, v) for t, v in zip(ts, self.counts) if v]
        return {"MetricDataResults": [
            {"Id": "api", "Timestamps": [t for t, _ in p], "Values": [v for _, v in p]},
            {"Id": "n", "Timestamps": [t for t, _ in c], "Values": [v for _, v in c]}]}


def test_recovery_needs_two_good_minutes_with_traffic():
    rec = lambda p, c: evidence.recovery(RecoveryCW(p, c), PARAMS["api_url"], 1000, END)  # noqa: E731
    r = rec([1600, 120, 110], [180, 180, 180])
    assert (r["recovered"], r["status"], r["api_p95_ms"], r["requests"]) == (True, "recovered", [120, 110], 540)
    assert rec([1600, 110, 1500], [180, 180, 180])["status"] == "not-recovered"
    assert rec([110], [180])["status"] == "waiting"  # one minute isn't a trend


def test_no_traffic_is_not_a_failed_recovery():
    """Issue #15 (19/11/2026): the demo traffic ended a minute before the fix went live;
    the loop said the p95 hadn't recovered. With no requests there's nothing to judge."""
    # traffic until 8 minutes before the check, then nothing
    p95s = [1650] * 52 + [None] * 8
    counts = [180] * 52 + [0] * 8
    r = evidence.recovery(RecoveryCW(p95s, counts), PARAMS["api_url"], 1000, END)
    assert (r["recovered"], r["status"], r["requests"]) == (False, "no-traffic", 0)
    assert r["no_traffic_since"] == (END - timedelta(minutes=9)).strftime("%H:%M")


def test_a_few_stray_requests_are_not_traffic():
    """One minute's p95 over two requests (a health check) can't confirm or refute."""
    r = evidence.recovery(RecoveryCW([None, None, None, None, 90], [0, 0, 0, 0, 2]), PARAMS["api_url"], 1000, END)
    assert r["status"] == "no-traffic" and not r["recovered"]


def test_slow_minutes_with_few_requests_are_ignored():
    r = evidence.recovery(RecoveryCW([120, 3000, 110], [180, 2, 180]), PARAMS["api_url"], 1000, END)
    assert r["status"] == "recovered" and r["api_p95_ms"] == [120, 110]
