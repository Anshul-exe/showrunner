"""A small AppConfig Data client with a poll interval (lane 1: runtime changes take
effect in under a minute, no deploy).

Why not the AWS AppConfig Lambda extension: it's a layer published from an AWS
account, pinned by version per architecture; this is ~50 lines, unit-tested, works
the same in both functions, and keeps the zip free of third-party binaries. The
behaviour is the same: one session per container, poll at most every
`poll_seconds`, keep serving the last good config if AppConfig is unreachable.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable


class AppConfigProfile:
    def __init__(
        self,
        application: str,
        environment: str,
        profile: str,
        defaults: dict[str, Any],
        client: Any = None,
        poll_seconds: int = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.application = application
        self.environment = environment
        self.profile = profile
        self._defaults = dict(defaults)
        self._client = client
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._token: str | None = None
        self._config: dict[str, Any] = dict(defaults)
        self._next_poll = 0.0
        self.last_error: str | None = None

    def _appconfigdata(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "appconfigdata",
                config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1}),
            )
        return self._client

    def get(self) -> dict[str, Any]:
        now = self._clock()
        if now < self._next_poll:
            return self._config
        self._next_poll = now + self._poll_seconds
        try:
            client = self._appconfigdata()
            if self._token is None:
                self._token = client.start_configuration_session(
                    ApplicationIdentifier=self.application,
                    EnvironmentIdentifier=self.environment,
                    ConfigurationProfileIdentifier=self.profile,
                    RequiredMinimumPollIntervalInSeconds=self._poll_seconds,
                )["InitialConfigurationToken"]
            resp = client.get_latest_configuration(ConfigurationToken=self._token)
            self._token = resp["NextPollConfigurationToken"]
            body = resp["Configuration"].read()
            if body:  # empty means "unchanged since the last poll"
                self._config = {**self._defaults, **json.loads(body)}
            self.last_error = None
        except Exception as exc:  # serve the last good config; never fail a request
            self._token = None
            self.last_error = type(exc).__name__
        return self._config
