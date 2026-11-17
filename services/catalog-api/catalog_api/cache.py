"""Valkey client with ElastiCache IAM authentication (no cache passwords anywhere).

The token is a SigV4-presigned `connect` request for this user and serverless cache,
signed with the Lambda role's credentials. It's valid for 15 minutes and is only
checked when a connection authenticates, so the credential provider mints a fresh one
for each new connection (and after the 12-hour IAM connection limit). See ADR 0006.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

import botocore.session
from botocore.model import ServiceId
from botocore.signers import RequestSigner

TOKEN_SECONDS = 900
REFRESH_AFTER = 600  # re-sign well before the 15-minute expiry


def iam_auth_token(cache_name: str, user_id: str, region: str, session=None) -> str:
    session = session or botocore.session.get_session()
    signer = RequestSigner(
        ServiceId("elasticache"),
        region,
        "elasticache",
        "v4",
        session.get_credentials(),
        session.get_component("event_emitter"),
    )
    query = urlencode({"Action": "connect", "User": user_id, "ResourceType": "ServerlessCache"})
    request = {
        "method": "GET",
        "url": f"https://{cache_name.lower()}/?{query}",
        "body": {},
        "headers": {},
        "context": {},
    }
    signed = signer.generate_presigned_url(
        request, operation_name="connect", expires_in=TOKEN_SECONDS, region_name=region
    )
    return signed.removeprefix("https://")


class IamCredentialProvider:
    """valkey-py CredentialProvider: called for every new connection."""

    def __init__(self, cache_name: str, user_id: str, region: str, clock=time.monotonic) -> None:
        self.cache_name = cache_name
        self.user_id = user_id
        self.region = region
        self._clock = clock
        self._token: str | None = None
        self._minted = 0.0

    def get_credentials(self):
        if self._token is None or self._clock() - self._minted > REFRESH_AFTER:
            self._token = iam_auth_token(self.cache_name, self.user_id, self.region)
            self._minted = self._clock()
        return self.user_id, self._token


def connect(endpoint: str, port: int, cache_name: str, user_id: str, region: str):
    """A cluster client: ElastiCache Serverless speaks the cluster protocol."""
    from valkey.cluster import ValkeyCluster
    from valkey.credentials import CredentialProvider

    class _Provider(IamCredentialProvider, CredentialProvider):
        pass

    return ValkeyCluster(
        host=endpoint,
        port=port,
        ssl=True,
        credential_provider=_Provider(cache_name, user_id, region),
        socket_timeout=0.5,
        socket_connect_timeout=1.0,
        cluster_error_retry_attempts=1,
        decode_responses=True,
    )
