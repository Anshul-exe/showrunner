"""Scenario S2 on dev: a bad release, rolled back by the canary (ADR 0016).

    uv run python -u tools/deploy/s2_drill.py --customer acme-tv [--rps 5] [--fail 0.2]

1. Builds catalog-api from main's source, then a *bad* copy of that zip whose /search
   raises on a share of requests (20% by default). The fault exists only in a temp
   file: nothing faulty is committed under services/.
2. Starts a probe first: GET /search at a fixed rate, recording each response's status
   and `x-sr-version` (which Lambda version answered), so the 10% split is measured.
3. After a warm-up, runs the normal canary (`lambda_deploy.py`) with the bad zip on one
   customer. Expected: `canary-errors` fires, the alias goes back to 100% old, and the
   old code is re-published as the newest version.
4. Keeps probing for a few minutes after, then writes the per-minute table and the
   deploy summary to --out.

dev only (CLAUDE.md rule 7). The probe is capped by the same rule as `sr demo traffic`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "deploy"), str(ROOT / "tools" / "build"), str(ROOT / "tools" / "sr")]

import lambda_deploy  # noqa: E402
import package  # noqa: E402

HANDLER = "catalog_api/handler.py"
SEARCH_BRANCH = '        if parts == ["search"]:\n'
TERMS = ["drama", "crime", "night", "football", "cup", "live", "north", "comedy"]


def make_bad(good: Path, out: Path, fail: float) -> Path:
    """A copy of `good` whose /search raises on `fail` of requests. Same layout and
    timestamps, so the only difference is the fault."""
    fault = (f'            if __import__("random").random() < {fail}:  # S2 drill fault (temp build only)\n'
             '                raise RuntimeError("S2 drill: a bad release")\n')
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == HANDLER:
                text = data.decode()
                if text.count(SEARCH_BRANCH) != 1:
                    raise SystemExit("can't find the /search branch in the handler: update s2_drill.py")
                data = text.replace(SEARCH_BRANCH, SEARCH_BRANCH + fault).encode()
            dst.writestr(info, data)
    return out


class Probe:
    """GET /search at `rps`; one record per response: (unix second, status, version)."""

    def __init__(self, url: str, rps: float):
        self.url, self.rps = url, rps
        self.rows: list[tuple[int, int, str]] = []
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def _hit(self) -> None:
        status, version = 0, "?"
        try:
            with urllib.request.urlopen(f"{self.url}/search?q={random.choice(TERMS)}", timeout=15) as r:  # noqa: S310
                status, version = r.status, r.headers.get("x-sr-version", "?")
        except urllib.error.HTTPError as e:
            status, version = e.code, e.headers.get("x-sr-version", "?")
        except (urllib.error.URLError, TimeoutError, OSError):
            status = -1
        with self.lock:
            self.rows.append((int(time.time()), status, version))

    def run(self) -> None:
        threads = []
        while not self.stop.is_set():
            t = threading.Thread(target=self._hit, daemon=True)
            t.start()
            threads.append(t)
            time.sleep(1 / self.rps)
        for t in threads:
            t.join(timeout=20)


def per_minute(rows: list[tuple[int, int, str]]) -> list[dict]:
    """{minute (UTC HH:MM), version: {n, errors}} per minute, oldest first."""
    acc: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for sec, status, version in rows:
        m = time.strftime("%H:%M", time.gmtime(sec))
        acc[m][version][0] += 1
        acc[m][version][1] += status >= 500 or status < 0
    return [{"minute": m, **{v: {"n": n, "errors": e} for v, (n, e) in sorted(vs.items())}}
            for m, vs in sorted(acc.items())]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--customer", default="acme-tv")
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--fail", type=float, default=0.2)
    ap.add_argument("--warmup", type=int, default=90, help="seconds of probe before the canary")
    ap.add_argument("--after", type=int, default=180, help="seconds of probe after the deploy ends")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "demo" / "s2")
    a = ap.parse_args(argv)
    import common  # boto3 + the account pin

    common.check_account()
    if a.rps > 10:
        raise SystemExit("--rps above 10: not needed for S2 (the canary sees 10% of it)")
    if not 0 < a.fail <= 0.5:
        raise SystemExit("--fail must be in (0, 0.5]")
    if profile := common.profile_name():
        import os

        os.environ["AWS_PROFILE"] = profile
    url = common.customer_params(a.customer, "dev")["api_url"].rstrip("/")

    with tempfile.TemporaryDirectory() as tmp:
        good = package.build("catalog-api", Path(tmp))
        bad = make_bad(good, Path(tmp) / "catalog-api-s2-bad.zip", a.fail)
        print(f"good {package.source_hash(good)}  bad {package.source_hash(bad)} (fails {a.fail:.0%} of /search)")
        probe = Probe(url, a.rps)
        th = threading.Thread(target=probe.run, daemon=True)
        started = time.time()
        th.start()
        print(time.strftime("%H:%M:%S", time.gmtime()), f"probe: {a.rps} rps of /search on {a.customer}; "
              f"canary in {a.warmup} s", flush=True)
        time.sleep(a.warmup)
        print(time.strftime("%H:%M:%S", time.gmtime()), "canary: bad build", flush=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = lambda_deploy.main(["--zip", str(bad), "--env", "dev", "--customer", a.customer])
        deploy_out = buf.getvalue()
        print(deploy_out, flush=True)
        deploy_end = time.time()
        time.sleep(a.after)
        probe.stop.set()
        th.join(timeout=30)

    table = per_minute(probe.rows)
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M", time.gmtime(started))
    record = {"customer": a.customer, "rps": a.rps, "fail": a.fail, "started_utc": time.strftime("%H:%M:%S", time.gmtime(started)),
              "deploy_end_utc": time.strftime("%H:%M:%S", time.gmtime(deploy_end)), "deploy_rc": rc,
              "deploy_output": deploy_out, "per_minute": table}
    path = a.out / f"s2-{stamp}.json"
    path.write_text(json.dumps(record, indent=1))
    for row in table:
        print(row["minute"], " ".join(f"v{v}: {d['n']} req, {d['errors']} err" for v, d in row.items() if v != "minute"))
    print(f"record: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
