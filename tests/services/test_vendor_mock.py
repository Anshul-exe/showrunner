import json

from vendor_mock.handler import handle, recommend


def event(customer="acme-tv", title="t01", path="/recs"):
    return {"rawPath": path, "headers": {"X-Customer": customer}, "queryStringParameters": {"title": title}}


def run(ev, chaos=None, rand=0.5):
    slept = []
    resp = handle(ev, lambda c: chaos or {}, sleep=slept.append, rand=lambda: rand)
    return resp["statusCode"], json.loads(resp["body"]), slept[0] if slept else None


def test_normal_latency_and_stable_recs():
    status, body, slept = run(event())
    assert status == 200 and body["recs"] == recommend("t01") and "t01" not in body["recs"]
    assert 0.03 <= slept <= 0.08


def test_chaos_latency_is_added():
    _, _, slept = run(event(), chaos={"vendor_latency_ms": 1500})
    assert slept >= 1.5


def test_chaos_latency_is_capped():
    _, _, slept = run(event(), chaos={"vendor_latency_ms": 999999})
    assert slept <= 5.1


def test_chaos_errors():
    assert run(event(), chaos={"vendor_error_rate": 0.6}, rand=0.5)[0] == 503
    assert run(event(), chaos={"vendor_error_rate": 0.4}, rand=0.5)[0] == 200


def test_bad_input():
    assert run(event(customer="Bad Customer"))[0] == 400
    assert run(event(path="/other"))[0] == 404
