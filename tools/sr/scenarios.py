"""Demo scenarios (dev only). Each one is a trigger and a reset, nothing else: the
platform's own alarms, dashboards and (day 2) incident loop do the rest.

S1, slow external vendor (the P9 story): +1500 ms on acme-tv's vendor calls. The
vendor alarm should fire in about 3 minutes; the per-dependency view points at the
vendor while the legacy view shows rising cache connections.
"""

from __future__ import annotations

import json

import appconfig

S1_CUSTOMER = "acme-tv"
S1_LATENCY_MS = 1500


def s1(action: str | None, env: str) -> int:
    if action == "start":
        r = appconfig.set_keys(S1_CUSTOMER, env, "chaos", {"vendor_latency_ms": S1_LATENCY_MS}, "S1 start: vendor +1500 ms")
        print(f"S1 started: {S1_CUSTOMER} vendor latency +{S1_LATENCY_MS} ms (deployment #{r['deployment']}, live in {r['seconds']} s).")
        print("Run traffic (./sr demo traffic) and watch (./sr demo watch): the vendor alarm should fire in ~3 min.")
        return 0
    if action == "stop":
        for profile, changes in (("chaos", {"vendor_latency_ms": 0, "vendor_error_rate": 0}), ("flags", {"vendor_fallback": False})):
            current, _ = appconfig.deployed(S1_CUSTOMER, env, profile)
            if all(current.get(k) == v for k, v in changes.items()):
                continue
            r = appconfig.set_keys(S1_CUSTOMER, env, profile, changes, "S1 stop: back to defaults")
            print(f"{profile}: {json.dumps(r['after'])} (deployment #{r['deployment']})")
        print("S1 stopped: chaos off, flags at defaults.")
        return 0
    print("usage: sr demo s1 start|stop")
    return 2
