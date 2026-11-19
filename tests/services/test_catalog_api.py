import io
import json
import os

import boto3
import pytest
from moto import mock_aws

from catalog_api.handler import FLAG_DEFAULTS, Service
from catalog_api.store import Store
from catalog_api.vendor import VendorError
from srkit.log import Logger
from srkit.metrics import Dependencies
from tests.services.fakes import FakeValkey, FakeVendor

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def aws():
    with mock_aws():
        yield


def make_table():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(
        TableName="sr-acme-tv-dev-catalog",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    for i, (name, genre) in enumerate([("Night Shift", "drama"), ("Harbour Lights", "crime"), ("Polar Run", "documentary")], 1):
        table.put_item(Item={"pk": "title", "sk": f"t{i:02d}", "name": name, "genre": genre, "year": 2020 + i})
    table.put_item(Item={"pk": "recs", "sk": "t01", "recs": ["t03", "t02"]})
    return table


def make_service(cache=None, vendor=None, flags=None):
    if cache is None:
        cache = FakeValkey()
    table = make_table()
    out = io.StringIO()
    svc = Service(
        customer="acme-tv",
        env="dev",
        store=Store(table),
        vendor=vendor or FakeVendor(),
        flags=lambda: {**FLAG_DEFAULTS, **(flags or {})},
        cache_factory=lambda: cache,
        log=Logger(),
        deps=Dependencies("acme-tv", "dev", stream=out),
    )
    return svc, cache, out


def get(svc, path, query=None):
    resp = svc.handle({"rawPath": path, "requestContext": {"http": {"method": "GET"}}, "queryStringParameters": query}, "r")
    return resp["statusCode"], json.loads(resp["body"])


def emf(out):
    return {d["dependency"]: d for d in map(json.loads, out.getvalue().splitlines())}


def test_catalog_miss_then_hit():
    svc, cache, out = make_service()
    status, body = get(svc, "/catalog")
    assert status == 200 and [t["id"] for t in body["titles"]] == ["t01", "t02", "t03"]
    assert "acme-tv:catalog" in cache.data  # key prefix = the customer's ACL scope
    assert set(emf(out)) == {"valkey", "dynamodb"}
    out.truncate(0), out.seek(0)
    get(svc, "/catalog")
    assert set(emf(out)) == {"valkey"}  # served from the cache, no DynamoDB call


def test_all_keys_are_customer_prefixed():
    svc, cache, _ = make_service()
    get(svc, "/catalog"), get(svc, "/titles/t02"), get(svc, "/titles/t01/recs")
    assert cache.data and all(k.startswith("acme-tv:") for k in cache.data)


def test_title_and_404():
    svc, _, _ = make_service()
    assert get(svc, "/titles/t02")[1]["name"] == "Harbour Lights"
    assert get(svc, "/titles/t99")[0] == 404
    assert get(svc, "/titles/BAD!")[0] == 400


def test_search():
    svc, _, _ = make_service()
    _, body = get(svc, "/search", {"q": "crime"})
    assert [t["id"] for t in body["titles"]] == ["t02"]


def test_recs_from_vendor_refresh_fallback_copy_once():
    vendor = FakeVendor(recs=["t02", "t03"])
    svc, _, out = make_service(vendor=vendor)
    status, body = get(svc, "/titles/t01/recs")
    assert status == 200 and body == {"title": "t01", "recs": ["t02", "t03"], "source": "vendor"}
    assert svc.store.get_cached_recs("t01") == ["t02", "t03"]
    svc.store.put_cached_recs("t01", ["old"])
    get(svc, "/titles/t01/recs")
    assert svc.store.get_cached_recs("t01") == ["old"]  # marker still set: no second write
    assert "vendor-recs" in emf(out)


def test_vendor_fallback_flag_skips_the_vendor():
    vendor = FakeVendor()
    svc, _, _ = make_service(vendor=vendor, flags={"vendor_fallback": True})
    _, body = get(svc, "/titles/t01/recs")
    assert body["source"] == "fallback" and body["recs"] == ["t03", "t02"]
    assert vendor.calls == 0


def test_vendor_error_serves_cached_recs_and_counts_error():
    svc, _, out = make_service(vendor=FakeVendor(error=VendorError("vendor HTTP 503")))
    status, body = get(svc, "/titles/t01/recs")
    assert status == 200 and body["source"] == "fallback-on-error" and body["recs"] == ["t03", "t02"]
    assert emf(out)["vendor-recs"]["DependencyErrors"] == 1


def test_cache_down_degrades_to_dynamodb():
    svc, _, out = make_service(cache=FakeValkey(fail=True))
    status, body = get(svc, "/catalog")
    assert status == 200 and len(body["titles"]) == 3
    assert emf(out)["valkey"]["DependencyErrors"] >= 1


def test_slow_vendor_shows_in_vendor_latency_not_valkey():
    """The P9 story in miniature: the per-dependency metric points at the vendor."""
    svc, _, out = make_service(vendor=FakeVendor(delay_s=0.2))
    get(svc, "/titles/t01/recs")
    m = emf(out)
    assert max(m["vendor-recs"]["DependencyLatency"]) >= 200
    assert max(m["valkey"]["DependencyLatency"]) < 50


def test_unhandled_error_is_500_without_leaking():
    svc, _, _ = make_service()
    svc.store = None  # force an AttributeError deep inside
    svc._cache_factory = lambda: None
    status, body = get(svc, "/catalog")
    assert status == 500 and body == {"error": "internal error"}


def test_health_has_no_dependencies():
    svc, _, out = make_service()
    assert get(svc, "/health")[1]["ok"] is True
    assert out.getvalue() == ""


def test_warm_connects_outside_request_metrics():
    svc, cache, out = make_service()
    svc.warm()
    assert svc._cache is cache
    assert out.getvalue() == ""  # connection setup isn't a request's valkey latency


def test_warm_failure_is_not_fatal():
    svc, _, _ = make_service(cache=FakeValkey(fail=True))
    svc._cache_factory = lambda: (_ for _ in ()).throw(ConnectionError("no route"))
    svc.warm()
    assert get(svc, "/catalog")[0] == 200


def test_vendor_url_flag_overrides_the_configured_url():
    """S4's lever: a runtime flag can point the vendor client elsewhere (empty = default)."""
    vendor = FakeVendor()
    flags = {}
    svc, _, _ = make_service(vendor=vendor, flags=flags)
    get(svc, "/titles/t01/recs")
    flags["vendor_url"] = "https://typo.lambda-ulr.us-east-1.on.aws"
    get(svc, "/titles/t02/recs")
    assert vendor.urls == [None, "https://typo.lambda-ulr.us-east-1.on.aws"]


def test_cache_connection_is_recycled_after_max_age():
    """Established connections survive a revoked security-group rule; recycling them
    makes such a change (and a fresh IAM token) take effect within the max age."""
    made = []

    def factory():
        made.append(FakeValkey())
        return made[-1]

    svc, _, _ = make_service()
    svc._cache_factory = factory
    now = [1000.0]
    svc._clock = lambda: now[0]
    svc.cache_get("acme-tv:x")
    now[0] += 60
    svc.cache_get("acme-tv:x")
    assert len(made) == 1
    now[0] += svc.cache_max_age_s
    svc.cache_get("acme-tv:x")
    assert len(made) == 2 and made[0].closed


def test_cache_breaker_skips_the_cache_after_a_failure_then_retries():
    """S3 drill finding: without a breaker every request paid two failed connects and
    hit the Lambda timeout. With it: one failed call, then DynamoDB only (each skip
    still counted as a valkey error), then one retry after 30 s."""
    made = []

    def factory():
        made.append(FakeValkey(fail=True))
        return made[-1]

    svc, _, out = make_service()
    svc._cache_factory = factory
    now = [1000.0]
    svc._clock = lambda: now[0]
    for _ in range(3):
        status, body = get(svc, "/titles/t01")
        assert status == 200
    assert len(made) == 1  # only the first request tried to connect
    last = emf(out)["valkey"]  # the third request: both cache calls skipped, both counted
    assert last["DependencyErrors"] == 2 and max(last["DependencyLatency"]) < 5
    now[0] += svc.cache_retry_after_s + 1
    get(svc, "/titles/t01")
    assert len(made) == 2  # breaker closed: one new attempt
