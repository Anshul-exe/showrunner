import base64
import io
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from incident import github_app


def _key():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                          serialization.NoEncryption()).decode()
    return k, pem


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_jwt_is_rs256_and_short_lived():
    k, pem = _key()
    token = github_app.app_jwt("5807966", pem, now=1_000_000)
    h, c, s = token.split(".")
    assert json.loads(_unb64(h)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(_unb64(c)) == {"iat": 999_940, "exp": 1_000_540, "iss": "5807966"}
    k.public_key().verify(_unb64(s), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_installation_token_cached_and_scoped_to_the_repo():
    _, pem = _key()
    calls = []

    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        if req.full_url.endswith("/access_tokens"):
            return FakeResp(json.dumps({"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}).encode())
        return FakeResp(json.dumps({"number": 7, "html_url": "https://github.com/o/r/issues/7"}).encode())

    gh = github_app.GitHubApp("1", "2", pem, "owner/showrunner", opener=opener)
    assert gh.create_issue("t", "b", ["incident"]) == (7, "https://github.com/o/r/issues/7")
    gh.comment(7, "hello")
    token_calls = [c for c in calls if c[1].endswith("/access_tokens")]
    assert len(token_calls) == 1 and token_calls[0][2] == {"repositories": ["showrunner"]}
