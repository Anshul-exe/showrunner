"""P8: a request carrying credentials and personal data must leave no trace in logs."""

import io
import json

import pytest

from srkit.log import Logger, scrub

SECRETS = [
    "Bearer abc.def.ghi-secret-token",
    "session=s3cr3t-cookie-value",
    "viewer@example.com",
    "AKIAIOSFODNN7EXAMPLE",
    # A fake JWT, assembled at runtime so secret scanners don't flag the fixture.
    ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NSJ9", "c2lnbmF0dXJl"]),
]


def test_disallowed_fields_are_dropped_not_logged():
    out = io.StringIO()
    Logger(out).info(
        "request",
        route="/catalog",
        headers={"Authorization": SECRETS[0], "Cookie": SECRETS[1]},
        authorization=SECRETS[0],
        cookie=SECRETS[1],
        email=SECRETS[2],
    )
    text = out.getvalue()
    for s in SECRETS[:3]:
        assert s not in text
    record = json.loads(text)
    assert record["route"] == "/catalog"
    assert record["dropped_fields"] == 4
    assert "headers" not in record and "authorization" not in record


# A bare cookie value has no recognisable shape: the allowlist is what keeps it out.
@pytest.mark.parametrize("secret", [s for s in SECRETS if not s.startswith("session=")])
def test_allowed_fields_are_scrubbed(secret):
    out = io.StringIO()
    Logger(out).warning(f"upstream said {secret}", error=f"failed for {secret}")
    assert secret not in out.getvalue()


def test_containers_are_never_logged_even_in_allowed_fields():
    out = io.StringIO()
    Logger(out).info("x", error={"Authorization": SECRETS[0]})
    assert SECRETS[0] not in out.getvalue()


def test_scrub_masks_email():
    assert scrub("contact viewer@example.com now") == "contact [email] now"


def test_catalog_api_request_logs_contain_no_secrets(capsys):
    """End to end through the real handler: headers, cookies and an email in the query."""
    from moto import mock_aws

    from tests.services.test_catalog_api import make_service

    with mock_aws():
        svc, _, _ = make_service()
        resp = svc.handle(EVENT, "req-1")
    assert resp["statusCode"] == 200
    logs = capsys.readouterr().out
    assert logs  # something was logged
    for s in SECRETS[:3] + ["203.0.113.9"]:
        assert s not in logs


EVENT = {
    "rawPath": "/search",
    "requestContext": {"http": {"method": "GET", "sourceIp": "203.0.113.9"}},
    "headers": {"authorization": SECRETS[0], "cookie": SECRETS[1], "x-viewer": SECRETS[2]},
    "cookies": [SECRETS[1]],
    "queryStringParameters": {"q": SECRETS[2]},
}

