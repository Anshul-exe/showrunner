"""Evidence shaped like the real S1 run (17/11/2026): the vendor slow, everything else flat."""

from __future__ import annotations

import copy


def _dep(eid, dep, now, base, errors=0):
    return {"id": eid, "kind": "dependency_latency", "summary": f"{dep} p95 {now} ms vs {base} ms",
            "source": "test", "data": {"dependency": dep, "now": now, "baseline": base, "points": 8, "errors_5m": errors}}


S1 = [
    {"id": "E1", "kind": "alarm", "summary": "sr-acme-tv-dev-vendor-latency entered ALARM", "source": "test",
     "data": {"name": "sr-acme-tv-dev-vendor-latency", "alarm_kind": "vendor-latency"}},
    {"id": "E2", "kind": "api_latency", "summary": "API p95 1,650 ms vs 110 ms", "source": "test",
     "data": {"now": 1650.0, "baseline": 110.0, "points": 8}},
    _dep("E3", "valkey", 1.6, 1.5),
    _dep("E4", "dynamodb", 9.0, 9.2),
    _dep("E5", "vendor-recs", 1600.0, 95.0),
    {"id": "E6", "kind": "valkey_server_latency", "summary": "read p99 1,180 µs", "source": "test",
     "data": {"read_p99_us": 1180.0, "write_p99_us": 1900.0}},
    {"id": "E7", "kind": "config_changes", "summary": "none", "source": "test", "data": {"changes": []}},
    {"id": "E8", "kind": "runtime_flags", "summary": "flags", "source": "test",
     "data": {"flags": {"vendor_fallback": False, "catalog_ttl_s": 300, "recs_ttl_s": 120}, "version": 5,
              "at_defaults": True}},
    {"id": "E9", "kind": "deploys", "summary": "none", "source": "test", "data": {"versions": [], "live": "3"}},
]

ACTIONS = [
    {"id": "vendor-fallback-on", "lane": "runtime", "applies_to": "vendor-recs",
     "appconfig": {"profile": "flags", "set": {"vendor_fallback": True}},
     "blast_radius": "One customer env.", "rollback": {"profile": "flags", "set": {"vendor_fallback": False}}},
    {"id": "rollback-last-appconfig-deployment", "lane": "runtime", "applies_to": "config-change",
     "blast_radius": "One customer env.", "rollback": "Redeploy it."},
]


def s1():
    return copy.deepcopy(S1)


def with_change(minutes_before=8, at_defaults=False):
    ev = s1()
    ev[6]["data"]["changes"] = [{"deployment": 9, "state": "COMPLETE", "started": "22:10:00",
                                 "minutes_before_alarm": minutes_before, "description": "x"}]
    if not at_defaults:
        ev[7]["data"]["flags"]["catalog_ttl_s"] = 5
    ev[7]["data"]["at_defaults"] = at_defaults
    return ev
