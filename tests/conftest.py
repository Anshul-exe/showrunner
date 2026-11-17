import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("services/shared", "services/catalog-api", "services/vendor-mock", "tools", "ops/lib", "ops/incident"):
    sys.path.insert(0, str(ROOT / p))


import pytest  # noqa: E402

# Tests must never reach a real AWS account. Without this, a helper that forgets
# `mock_aws` would run against whatever profile the shell has (it did once: it created
# a real table). Fake credentials + no profile = such a call fails instead.
_FAKE_AWS = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch):
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in _FAKE_AWS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
