"""Demo traffic from the operator's machine: a viewer-like mix at a low, capped rate.

Little's law: Lambda concurrency = requests/s x seconds per request. Until the account's
concurrency quota is raised from 10, a slow vendor (1.5 s) holds a catalog-api *and* a
vendor-mock instance per recommendation request, so the rate stays low and capped.
"""

from __future__ import annotations

import random
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import client, customer_params

MIX = [  # (weight, path template)
    (35, "/catalog"),
    (20, "/titles/{id}"),
    (15, "/search?q={term}"),
    (30, "/titles/{id}/recs"),
]
TERMS = ["drama", "crime", "night", "football", "hockey", "cup", "live", "north", "comedy", "ski"]
TITLES = [f"t{n:02d}" for n in range(1, 13)]
SMALL_QUOTA_MAX_RPS = 4.0


def max_rps() -> float:
    quota = client("lambda").get_account_settings()["AccountLimit"]["ConcurrentExecutions"]
    return SMALL_QUOTA_MAX_RPS if quota <= 10 else 20.0


def pick_path(rng: random.Random) -> str:
    template = rng.choices([p for _, p in MIX], weights=[w for w, _ in MIX])[0]
    return template.format(id=rng.choice(TITLES), term=rng.choice(TERMS))


def run(customers: list[str], env: str, minutes: float, rps: float) -> None:
    cap = max_rps()
    if rps > cap:
        print(f"capping {rps} rps to {cap}: Lambda concurrency quota is small (Little's law)")
        rps = cap
    urls = {c: customer_params(c, env)["api_url"] for c in customers}
    rng = random.Random()
    lock = threading.Lock()
    stats: dict[str, list] = {c: [] for c in customers}
    errors: dict[str, int] = {c: 0 for c in customers}

    def hit(customer: str, path: str) -> None:
        start = time.perf_counter()
        ok = True
        try:
            with urllib.request.urlopen(urls[customer] + path, timeout=15) as r:  # noqa: S310
                r.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            ok = False
        elapsed = time.perf_counter() - start
        with lock:
            stats[customer].append(elapsed)
            if not ok:
                errors[customer] += 1

    end = time.time() + minutes * 60
    interval = 1.0 / rps
    next_report = time.time() + 15
    print(f"traffic: {rps} rps across {', '.join(customers)} for {minutes} min (Ctrl-C to stop)")
    with ThreadPoolExecutor(max_workers=16) as pool:
        try:
            while time.time() < end:
                pool.submit(hit, rng.choice(customers), pick_path(rng))
                time.sleep(interval)
                if time.time() >= next_report:
                    next_report += 15
                    with lock:
                        line = []
                        for c in customers:
                            s = sorted(stats[c])
                            if s:
                                p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
                                line.append(f"{c}: n={len(s)} err={errors[c]} median={statistics.median(s):.2f}s p95={p95:.2f}s")
                            stats[c].clear()
                            errors[c] = 0
                    print(time.strftime("%H:%M:%S"), " | ".join(line), "(client side, incl. internet RTT)", flush=True)
        except KeyboardInterrupt:
            print("stopping")
