"""GitHub App client: the bot identity of the incident loop. No personal access token.

Auth: a JWT signed with the App's private key (SSM SecureString, read at runtime) is
exchanged for a 1-hour installation token scoped to this one repository.
"""

from __future__ import annotations

import base64
import calendar
import json
import time
import urllib.error
import urllib.request
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

API = "https://api.github.com"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, private_key_pem: str, now: int | None = None) -> str:
    now = int(now if now is not None else time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    # iat 60 s in the past for clock drift; GitHub allows at most 10 minutes.
    claims = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(sig)}"


class GitHubError(Exception):
    pass


class GitHubApp:
    def __init__(self, app_id: str, installation_id: str, private_key_pem: str, repo: str,
                 opener=urllib.request.urlopen):
        self.app_id, self.installation_id, self.key, self.repo = app_id, installation_id, private_key_pem, repo
        self._open = opener
        self._token: tuple[str, float] | None = None

    def _call(self, method: str, url: str, token: str, body: dict | None = None) -> Any:
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "showrunner-incident-loop")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout=15) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"{method} {url.replace(API, '')}: HTTP {exc.code}") from None
        return json.loads(raw) if raw else None

    def token(self) -> str:
        if self._token and self._token[1] > time.time() + 300:
            return self._token[0]
        jwt = app_jwt(self.app_id, self.key)
        resp = self._call("POST", f"{API}/app/installations/{self.installation_id}/access_tokens", jwt,
                          {"repositories": [self.repo.split("/", 1)[1]]})
        expires = calendar.timegm(time.strptime(resp["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
        self._token = (resp["token"], expires)
        return resp["token"]

    def api(self, method: str, path: str, body: dict | None = None) -> Any:
        return self._call(method, f"{API}/repos/{self.repo}{path}", self.token(), body)

    def create_issue(self, title: str, body: str, labels: list[str]) -> tuple[int, str]:
        r = self.api("POST", "/issues", {"title": title, "body": body, "labels": labels})
        return r["number"], r["html_url"]

    def comment(self, number: int, body: str) -> str:
        return self.api("POST", f"/issues/{number}/comments", {"body": body})["html_url"]

    def add_labels(self, number: int, labels: list[str]) -> None:
        self.api("POST", f"/issues/{number}/labels", {"labels": labels})

    def close(self, number: int) -> None:
        self.api("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})
