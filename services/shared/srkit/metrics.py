"""Per-dependency metrics (P9): every call to Valkey, DynamoDB or the vendor goes
through `Dependencies.call()`, which records its latency and whether it failed.

`flush()` writes one CloudWatch Embedded Metric Format (EMF) document per dependency
to stdout. CloudWatch Logs turns it into two metrics:

    Showrunner / DependencyLatency  (Milliseconds)  {customer, env, dependency}
    Showrunner / DependencyErrors   (Count)         {customer, env, dependency}

p50/p95/p99 and call counts are statistics of the one latency metric, so three
dependencies x two customers = 12 custom metrics in total (BRIEF: keep it ~25).
No PutMetricData calls, no extra latency in the request path.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator, TextIO

NAMESPACE = "Showrunner"
MAX_VALUES = 100  # EMF limit per metric per document


class Dependencies:
    def __init__(self, customer: str, env: str, stream: TextIO | None = None) -> None:
        self.customer = customer
        self.env = env
        self._stream = stream
        self._latency: dict[str, list[float]] = defaultdict(list)
        self._errors: dict[str, int] = defaultdict(int)

    @contextmanager
    def call(self, dependency: str) -> Iterator[None]:
        """Time one call. An exception counts as an error and is re-raised."""
        start = time.perf_counter()
        try:
            yield
        except Exception:
            self._errors[dependency] += 1
            raise
        finally:
            self._latency[dependency].append(round((time.perf_counter() - start) * 1000, 3))

    def record_error(self, dependency: str) -> None:
        """For failures the caller handles without an exception (e.g. HTTP 5xx)."""
        self._errors[dependency] += 1

    def latencies(self, dependency: str) -> list[float]:
        return list(self._latency.get(dependency, []))

    def errors(self, dependency: str) -> int:
        return self._errors.get(dependency, 0)

    def flush(self) -> None:
        now_ms = int(time.time() * 1000)
        for dependency in sorted(set(self._latency) | set(self._errors)):
            doc = {
                "_aws": {
                    "Timestamp": now_ms,
                    "CloudWatchMetrics": [
                        {
                            "Namespace": NAMESPACE,
                            "Dimensions": [["customer", "env", "dependency"]],
                            "Metrics": [
                                {"Name": "DependencyLatency", "Unit": "Milliseconds"},
                                {"Name": "DependencyErrors", "Unit": "Count"},
                            ],
                        }
                    ],
                },
                "customer": self.customer,
                "env": self.env,
                "dependency": dependency,
                "DependencyLatency": self._latency.get(dependency, [])[-MAX_VALUES:],
                "DependencyErrors": self._errors.get(dependency, 0),
            }
            print(json.dumps(doc, separators=(",", ":")), file=self._stream or sys.stdout)
        self._latency.clear()
        self._errors.clear()
