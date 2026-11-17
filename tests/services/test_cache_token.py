from urllib.parse import parse_qs, urlparse

import botocore.session

from catalog_api.cache import IamCredentialProvider, iam_auth_token


def session():
    s = botocore.session.get_session()
    s.set_credentials("AKIDEXAMPLE", "secret", "token")
    return s


def test_token_is_presigned_connect_for_serverless_cache():
    token = iam_auth_token("SR-Dev-Cache", "sr-acme-tv-dev", "us-east-1", session=session())
    assert not token.startswith("http")
    parsed = urlparse("https://" + token)
    assert parsed.hostname == "sr-dev-cache"  # cache names are lowercase
    q = parse_qs(parsed.query)
    assert q["Action"] == ["connect"]
    assert q["User"] == ["sr-acme-tv-dev"]
    assert q["ResourceType"] == ["ServerlessCache"]
    assert q["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in q
    assert q["X-Amz-Credential"][0].split("/")[3] == "elasticache"


def test_provider_reuses_token_until_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr("catalog_api.cache.iam_auth_token", lambda *a: calls.append(a) or f"tok{len(calls)}")
    t = [0.0]
    p = IamCredentialProvider("c", "u", "us-east-1", clock=lambda: t[0])
    assert p.get_credentials() == ("u", "tok1")
    t[0] = 300
    assert p.get_credentials() == ("u", "tok1")
    t[0] = 700
    assert p.get_credentials() == ("u", "tok2")
