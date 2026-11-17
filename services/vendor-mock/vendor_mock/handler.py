"""vendor-mock: a pretend external recommendations vendor.

It's a public Lambda function URL *outside* the VPC, so catalog-api reaches it the way
it would reach a real vendor: out of the private subnets, through the NAT instance,
over public HTTPS.

Chaos (dev only, the S1 scenario): latency and error rate come from the calling
customer's AppConfig `chaos` profile (app `sr-<customer>-<env>`), so a demo makes the
vendor slow for one customer without touching code or infrastructure. AWS Fault
Injection Service isn't on the Free plan; this is the substitute.

    GET /recs?title=<id>      header x-customer: <customer>
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from typing import Any, Callable

from srkit.appconfig import AppConfigProfile
from srkit.log import Logger

CHAOS_DEFAULTS = {"vendor_latency_ms": 0, "vendor_error_rate": 0.0}
CUSTOMER = re.compile(r"^[a-z0-9-]{1,32}$")
TITLE = re.compile(r"^[a-z0-9-]{1,32}$")
CATALOG = [f"t{n:02d}" for n in range(1, 13)]
MAX_LATENCY_MS = 5000

_profiles: dict[str, AppConfigProfile] = {}
_log = Logger(service="vendor-mock", env=os.environ.get("ENV", "dev"))


def recommend(title_id: str, count: int = 5) -> list[str]:
    """Deterministic 'recommendations': stable for a title, different across titles."""
    seed = int(hashlib.sha256(title_id.encode()).hexdigest(), 16)
    others = [t for t in CATALOG if t != title_id]
    rng = random.Random(seed)  # noqa: S311 (not security relevant)
    return rng.sample(others, k=min(count, len(others)))


def chaos_for(customer: str, env: str) -> dict[str, Any]:
    if customer not in _profiles:
        _profiles[customer] = AppConfigProfile(
            f"sr-{customer}-{env}", env, "chaos", CHAOS_DEFAULTS,
            poll_seconds=int(os.environ.get("APPCONFIG_POLL_SECONDS", "15")),
        )
    return _profiles[customer].get()


def respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, separators=(",", ":")),
    }


def handle(
    event: dict[str, Any],
    chaos: Callable[[str], dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> dict[str, Any]:
    path = event.get("rawPath", "/")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    query = event.get("queryStringParameters") or {}
    customer = headers.get("x-customer", "")
    title_id = query.get("title", "")

    if path != "/recs":
        return respond(404, {"error": "not found"})
    if not CUSTOMER.match(customer) or not TITLE.match(title_id):
        return respond(400, {"error": "x-customer header and title are required"})

    cfg = chaos(customer)
    latency_ms = min(max(int(cfg.get("vendor_latency_ms", 0)), 0), MAX_LATENCY_MS)
    error_rate = min(max(float(cfg.get("vendor_error_rate", 0.0)), 0.0), 1.0)

    # Normal vendor behaviour: 30-80 ms. Chaos adds on top.
    sleep((30 + rand() * 50 + latency_ms) / 1000)
    if rand() < error_rate:
        _log.warning("injected error", customer=customer, status=503)
        return respond(503, {"error": "vendor unavailable"})
    return respond(200, {"title": title_id, "recs": recommend(title_id)})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    env = os.environ.get("ENV", "dev")
    return handle(event, lambda c: chaos_for(c, env))
