"""Redacting JSON logger (P8: a shared logging library leaked auth headers, cookies
and personal data into widely readable logs).

Two layers:
1. Allowlist: only the field names below are ever written. Anything else (a header
   dict, a cookie, a whole event) is dropped, and only the *count* of dropped
   fields is logged, never their names or values.
2. Scrubbing: string values that are allowed still get emails, bearer tokens, JWTs
   and AWS access key IDs masked, in case one is embedded in an error message.

The CloudWatch Logs data-protection policy on each log group is the backstop.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any, TextIO

ALLOWED_FIELDS = frozenset(
    {
        "customer", "env", "service", "request_id", "method", "route", "status",
        "duration_ms", "dependency", "error", "error_type", "title_id", "source",
        "cache", "count", "query_len", "fallback", "profile", "latency_ms",
        "cold_start",
    }
)

_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [token]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[jwt]"),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[aws-key-id]"),
]


def scrub(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)[:500]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    # Containers could hold anything (headers, events): never log them.
    return f"[{type(value).__name__}]"


class Logger:
    def __init__(self, stream: TextIO | None = None, **context: Any) -> None:
        self._stream = stream
        self._context = context

    def bind(self, **context: Any) -> "Logger":
        return Logger(self._stream, **{**self._context, **context})

    def _emit(self, level: str, msg: str, fields: dict[str, Any]) -> None:
        merged = {**self._context, **fields}
        record: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": level,
            "msg": scrub(msg)[:500],
        }
        dropped = 0
        for key, value in merged.items():
            if key in ALLOWED_FIELDS:
                record[key] = _clean(value)
            else:
                dropped += 1
        if dropped:
            record["dropped_fields"] = dropped
        print(json.dumps(record, separators=(",", ":")), file=self._stream or sys.stdout)

    def info(self, msg: str, **fields: Any) -> None:
        self._emit("INFO", msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit("WARNING", msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._emit("ERROR", msg, fields)
