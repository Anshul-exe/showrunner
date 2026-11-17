"""catalog-api: one customer's catalog and recommendations service.

Request path (BRIEF Pillar 2):  Valkey (cache) -> DynamoDB on a miss -> vendor for recs.
Every dependency call is timed by srkit.metrics, so the per-dependency dashboard can
say *which* dependency is slow; the API's own latency can't.

Routes (HTTP API, payload v2):
    GET /health
    GET /catalog
    GET /titles/{id}
    GET /titles/{id}/recs
    GET /search?q=...
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from srkit.appconfig import AppConfigProfile
from srkit.log import Logger
from srkit.metrics import Dependencies

from catalog_api.store import Store
from catalog_api.vendor import VendorClient, VendorError

FLAG_DEFAULTS = {"vendor_fallback": False, "catalog_ttl_s": 300, "recs_ttl_s": 120}
TITLE_ID = re.compile(r"^[a-z0-9-]{1,32}$")


class Service:
    def __init__(
        self,
        customer: str,
        env: str,
        store: Store,
        vendor: VendorClient,
        flags: Callable[[], dict[str, Any]],
        cache_factory: Callable[[], Any] | None,
        log: Logger,
        deps: Dependencies,
    ) -> None:
        self.customer = customer
        self.env = env
        self.store = store
        self.vendor = vendor
        self.flags = flags
        self._cache_factory = cache_factory
        self._cache: Any = None
        self.log = log
        self.deps = deps

    # ------------------------------------------------------------------ cache
    def _key(self, *parts: str) -> str:
        # The customer's Valkey user may only touch keys under "<customer>:" (ACL).
        return ":".join((self.customer, *parts))

    def _client(self) -> Any:
        if self._cache is None and self._cache_factory is not None:
            self._cache = self._cache_factory()
        return self._cache

    def _cache_op(self, op: str, *args: Any, **kwargs: Any) -> Any:
        """A cache failure degrades to DynamoDB; it never fails the request."""
        try:
            with self.deps.call("valkey"):
                client = self._client()
                if client is None:
                    return None
                return getattr(client, op)(*args, **kwargs)
        except Exception as exc:
            self._cache = None  # reconnect (fresh IAM token) on the next call
            self.log.warning("cache unavailable", dependency="valkey", error_type=type(exc).__name__)
            return None

    def warm(self) -> None:
        """Open the Valkey connection during Lambda init, like a connection pool.

        Connecting costs ~1 s (TLS, IAM token, cluster discovery). Done here, it's part
        of the cold start, and the per-request `valkey` metric measures commands, not
        connection setup. If it fails, requests connect lazily and degrade to DynamoDB.
        """
        try:
            client = self._client()
            if client is not None:
                client.ping()
        except Exception as exc:
            self._cache = None
            self.log.warning("cache warm-up failed", dependency="valkey", error_type=type(exc).__name__)

    def cache_get(self, key: str) -> Any:
        raw = self._cache_op("get", key)
        return json.loads(raw) if raw else None

    def cache_set(self, key: str, value: Any, ttl: int) -> None:
        self._cache_op("set", key, json.dumps(value, separators=(",", ":")), ex=ttl)

    # ------------------------------------------------------------------ data
    def catalog(self) -> tuple[list[dict[str, Any]], str]:
        key = self._key("catalog")
        cached = self.cache_get(key)
        if cached is not None:
            return cached, "cache"
        with self.deps.call("dynamodb"):
            titles = sorted(self.store.list_titles(), key=lambda t: t["id"])
        self.cache_set(key, titles, int(self.flags()["catalog_ttl_s"]))
        return titles, "dynamodb"

    def title(self, title_id: str) -> tuple[dict[str, Any] | None, str]:
        key = self._key("title", title_id)
        cached = self.cache_get(key)
        if cached is not None:
            return cached, "cache"
        with self.deps.call("dynamodb"):
            item = self.store.get_title(title_id)
        if item:
            self.cache_set(key, item, int(self.flags()["catalog_ttl_s"]))
        return item, "dynamodb"

    def cached_recs(self, title_id: str) -> list[str]:
        with self.deps.call("dynamodb"):
            return self.store.get_cached_recs(title_id)

    def recommendations(self, title_id: str) -> tuple[list[str], str]:
        flags = self.flags()
        if flags["vendor_fallback"]:
            # Runtime lane: an operator (or an approved incident action) turned the
            # vendor off. Serve the last known recommendations; no deploy involved.
            return self.cached_recs(title_id), "fallback"
        try:
            with self.deps.call("vendor-recs"):
                recs = self.vendor.recommendations(title_id)
        except VendorError as exc:
            self.log.warning("vendor failed, serving cached recs", dependency="vendor-recs", error=str(exc))
            return self.cached_recs(title_id), "fallback-on-error"
        # Refresh the fallback copy at most once per recs_ttl_s per title. SET NX in
        # Valkey is the "recently refreshed" marker, so DynamoDB writes stay rare.
        marker = self._cache_op("set", self._key("recs-refreshed", title_id), "1", ex=int(flags["recs_ttl_s"]), nx=True)
        if marker:
            with self.deps.call("dynamodb"):
                self.store.put_cached_recs(title_id, recs)
        return recs, "vendor"

    # ------------------------------------------------------------------ http
    def route(self, method: str, path: str, query: dict[str, str]) -> tuple[int, dict[str, Any], dict[str, Any]]:
        """Returns (status, body, log fields)."""
        if method != "GET":
            return 405, {"error": "method not allowed"}, {"route": "other"}
        parts = [p for p in path.split("/") if p]
        if parts == ["health"]:
            return 200, {"ok": True, "customer": self.customer, "env": self.env}, {"route": "/health"}
        if parts == ["catalog"]:
            titles, source = self.catalog()
            return 200, {"titles": titles}, {"route": "/catalog", "source": source, "count": len(titles)}
        if parts == ["search"]:
            q = (query.get("q") or "").strip().lower()[:64]
            titles, source = self.catalog()
            hits = [t for t in titles if q and (q in t["name"].lower() or q in t["genre"].lower())]
            return 200, {"query": q, "titles": hits}, {"route": "/search", "source": source, "query_len": len(q), "count": len(hits)}
        if len(parts) in (2, 3) and parts[0] == "titles":
            title_id = parts[1]
            if not TITLE_ID.match(title_id):
                return 400, {"error": "bad title id"}, {"route": "/titles/{id}"}
            if len(parts) == 2:
                item, source = self.title(title_id)
                if item is None:
                    return 404, {"error": "not found"}, {"route": "/titles/{id}", "title_id": title_id}
                return 200, item, {"route": "/titles/{id}", "source": source, "title_id": title_id}
            if parts[2] == "recs":
                recs, source = self.recommendations(title_id)
                return 200, {"title": title_id, "recs": recs, "source": source}, {
                    "route": "/titles/{id}/recs", "source": source, "title_id": title_id,
                    "fallback": source != "vendor",
                }
        return 404, {"error": "not found"}, {"route": "other"}

    def handle(self, event: dict[str, Any], request_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET")
        path = event.get("rawPath", "/")
        query = event.get("queryStringParameters") or {}
        try:
            status, body, fields = self.route(method, path, query)
        except Exception as exc:
            status, body, fields = 500, {"error": "internal error"}, {"route": "error", "error_type": type(exc).__name__}
        finally:
            self.deps.flush()
        # Only allowlisted fields are written: the event (headers, cookies, source IP)
        # is never passed to the logger.
        self.log.info(
            "request", request_id=request_id, method=method, status=status,
            duration_ms=round((time.perf_counter() - start) * 1000, 1), **fields,
        )
        return {
            "statusCode": status,
            "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, separators=(",", ":")),
        }


# ---------------------------------------------------------------------- Lambda entry
def _build() -> Service:
    import boto3
    from botocore.config import Config

    from catalog_api import cache

    customer, env, region = os.environ["CUSTOMER"], os.environ["ENV"], os.environ["AWS_REGION"]
    ddb = boto3.resource(
        "dynamodb", config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 2})
    )
    flags = AppConfigProfile(
        os.environ["APPCONFIG_APPLICATION"], os.environ["APPCONFIG_ENVIRONMENT"], "flags",
        FLAG_DEFAULTS, poll_seconds=int(os.environ.get("APPCONFIG_POLL_SECONDS", "15")),
    )

    def cache_factory() -> Any:
        return cache.connect(
            os.environ["CACHE_ENDPOINT"], int(os.environ.get("CACHE_PORT", "6379")),
            os.environ["CACHE_NAME"], os.environ["CACHE_USER"], region,
        )

    service = Service(
        customer=customer,
        env=env,
        store=Store(ddb.Table(os.environ["TABLE_NAME"])),
        vendor=VendorClient(os.environ["VENDOR_URL"], customer, float(os.environ.get("VENDOR_TIMEOUT_S", "2.5"))),
        flags=flags.get,
        cache_factory=cache_factory,
        log=Logger(service="catalog-api", customer=customer, env=env),
        deps=Dependencies(customer, env),
    )
    service.warm()
    return service


# Built during the init phase (module import), before the first request is timed.
_service: Service | None = _build() if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _service
    if _service is None:
        _service = _build()
    return _service.handle(event, getattr(context, "aws_request_id", "-"))
