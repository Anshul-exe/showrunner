"""Demo traffic from the operator's machine: a viewer-like mix at a low, capped rate.

Little's law: Lambda concurrency = requests/s x seconds per request. Until the account's
concurrency quota is raised from 10, a slow vendor (1.5 s) holds a catalog-api *and* a
vendor-mock instance per recommendation request, so the rate stays low and capped.
"""

from __future__ import annotations

import random
import signal
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


def classify(exc: BaseException | None, status: int | None) -> str:
    """ok / throttled (429: API Gateway or Lambda concurrency) / 5xx / 4xx / network."""
    if exc is None:
        return "ok"
    if status == 429:
        return "throttled"
    if status is not None:
        return "5xx" if status >= 500 else "4xx"
    return "network"


def stop_message(reason: str, started: float, sent: int, totals: dict[str, int]) -> str:
    """The last line: why it stopped, so an early stop is never silent (18/11/2026)."""
    mins = (time.time() - started) / 60
    parts = ", ".join(f"{k}={v}" for k, v in totals.items() if v)
    return f"traffic stopped: {reason} after {mins:.1f} min; {sent} requests sent ({parts or 'no responses'})"


def run(customers: list[str], env: str, minutes: float, rps: float) -> str:
    cap = max_rps()
    if rps > cap:
        print(f"capping {rps} rps to {cap}: Lambda concurrency quota is small (Little's law)")
        rps = cap
    urls = {c: customer_params(c, env)["api_url"] for c in customers}
    rng = random.Random()
    lock = threading.Lock()
    stats: dict[str, list] = {c: [] for c in customers}
    errors: dict[str, int] = {c: 0 for c in customers}
    totals = {"ok": 0, "throttled": 0, "5xx": 0, "4xx": 0, "network": 0}
    stop = {"reason": None}

    def hit(customer: str, path: str) -> None:
        start = time.perf_counter()
        exc, status = None, None
        try:
            with urllib.request.urlopen(urls[customer] + path, timeout=15) as r:  # noqa: S310
                r.read()
        except urllib.error.HTTPError as e:
            exc, status = e, e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            exc = e
        elapsed = time.perf_counter() - start
        kind = classify(exc, status)
        with lock:
            stats[customer].append(elapsed)
            totals[kind] += 1
            if kind != "ok":
                errors[customer] += 1

    # A closed terminal (SIGHUP) or `kill` (SIGTERM) used to end the run with no message.
    def on_signal(signum, _frame):
        stop["reason"] = f"signal {signal.Signals(signum).name}"
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)

    started = time.time()
    end = started + minutes * 60
    interval = 1.0 / rps
    next_report = started + 15
    sent = 0
    print(f"traffic: {rps} rps across {', '.join(customers)} for {minutes} min (Ctrl-C to stop)", flush=True)
    pool = ThreadPoolExecutor(max_workers=16)
    try:
        while stop["reason"] is None:
            if time.time() >= end:
                stop["reason"] = f"--minutes {minutes:g} reached"
                break
            pool.submit(hit, rng.choice(customers), pick_path(rng))
            sent += 1
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
                    throttled = totals["throttled"]
                left = max(0.0, end - time.time()) / 60
                print(time.strftime("%H:%M:%S"), f"[{left:.1f} min left]", " | ".join(line),
                      f"(client side, incl. internet RTT; throttled so far: {throttled})", flush=True)
    except KeyboardInterrupt:
        stop["reason"] = "Ctrl-C"
    except Exception as exc:  # anything unexpected: say what, don't just vanish
        stop["reason"] = f"error {type(exc).__name__}: {exc}"
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        msg = stop_message(stop["reason"] or "unknown", started, sent, totals)
        try:
            print(msg, flush=True)
        except OSError:
            pass  # the terminal is gone (SIGHUP); nothing to print to
    return msg
