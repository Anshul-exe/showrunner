import io
import json

import pytest

from srkit.metrics import Dependencies


def docs(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_emf_document_per_dependency():
    out = io.StringIO()
    deps = Dependencies("acme-tv", "dev", stream=out)
    with deps.call("valkey"):
        pass
    with deps.call("vendor-recs"):
        pass
    with pytest.raises(RuntimeError), deps.call("vendor-recs"):
        raise RuntimeError("boom")
    deps.flush()

    by_dep = {d["dependency"]: d for d in docs(out)}
    assert set(by_dep) == {"valkey", "vendor-recs"}
    vendor = by_dep["vendor-recs"]
    assert vendor["customer"] == "acme-tv" and vendor["env"] == "dev"
    assert len(vendor["DependencyLatency"]) == 2
    assert vendor["DependencyErrors"] == 1
    assert by_dep["valkey"]["DependencyErrors"] == 0  # zero is emitted so alarms see data
    meta = vendor["_aws"]["CloudWatchMetrics"][0]
    assert meta["Namespace"] == "Showrunner"
    assert meta["Dimensions"] == [["customer", "env", "dependency"]]
    assert {m["Name"] for m in meta["Metrics"]} == {"DependencyLatency", "DependencyErrors"}


def test_flush_resets():
    out = io.StringIO()
    deps = Dependencies("c", "dev", stream=out)
    with deps.call("dynamodb"):
        pass
    deps.flush()
    deps.flush()
    assert len(docs(out)) == 1


def test_record_error_without_exception():
    out = io.StringIO()
    deps = Dependencies("c", "dev", stream=out)
    deps.record_error("vendor-recs")
    deps.flush()
    assert docs(out)[0]["DependencyErrors"] == 1
