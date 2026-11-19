"""Lane 2 for catalog-api: ship a zip to alias `live` by its SHA-256, through a canary
(ADR 0016).

    python3 tools/deploy/lambda_deploy.py --zip build/catalog-api.zip --env dev \
        --customer acme-tv [--customer ...] [--expect-sha256 <attested>] [--all-at-once]

Standard library and the AWS CLI only, so CI's deploy job installs nothing; `./sr deploy
catalog-api` runs the same code. Each customer's canary config (weight, minutes,
rollback alarms) comes from Terraform, via SSM `/showrunner/<env>/customers/<c>/canary`.

For each function:

1. skip it if `live` already runs this exact code (same CodeSha256): idempotent;
2. refuse to start while a rollback alarm is already firing;
3. upload the zip and publish a version **pinned to the zip's SHA-256** (PublishVersion
   with CodeSha256 fails if the code changed in between: deploy by digest);
4. canary: alias `live` stays on the old version and sends `weight` of requests to the
   new one (Lambda weighted alias routing) for `minutes`, checking the alarms every 10 s;
5. any alarm in ALARM: all traffic back to the old version, then re-publish the old
   code as the newest version (Terraform points `live` at the newest version on its next
   apply and must never bring the bad build back). Otherwise: 100% on the new version.

CodeDeploy does steps 4-5 as a service; it isn't on the AWS Free plan (ADR 0016). The
cost of doing it here: if this process is killed mid-canary, `live` stays split until
the next deploy or `./sr deploy` is re-run (an exception inside it rolls back).

Exit code: 0 all promoted (or already live), 1 any rolled back, refused or failed,
2 the zip isn't the attested one.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REGION = os.environ.get("AWS_REGION", "us-east-1")
POLL_S = 10


class AwsError(Exception):
    pass


def aws(args: list[str]) -> dict:
    """Run the AWS CLI (preinstalled on GitHub runners) and return its JSON output."""
    r = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise AwsError(r.stderr.strip().splitlines()[-1][:300] if r.stderr.strip() else f"aws exit {r.returncode}")
    return json.loads(r.stdout or "{}")


def code_sha256(data: bytes) -> str:
    """Lambda's CodeSha256: base64 of the SHA-256 of the zip."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


@dataclass
class Canary:
    weight: float = 0.1
    minutes: float = 5
    alarms: list[str] = field(default_factory=list)


@dataclass
class Rollout:
    function: str
    canary: Canary
    current: str = ""
    target: str | None = None
    status: str = "pending"
    seconds: float = 0.0
    restored: str | None = None
    detail: str = ""
    started: float = field(default_factory=time.time)
    deadline: float = 0.0


def customer_target(customer: str, env: str, call: Callable = aws) -> tuple[str, Canary]:
    """The function and its canary config, as Terraform published them."""
    path = f"/showrunner/{env}/customers/{customer}"
    ps = call(["ssm", "get-parameters", "--names", f"{path}/function_name", f"{path}/canary"])
    got = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in ps.get("Parameters", [])}
    if "function_name" not in got or "canary" not in got:
        raise AwsError(f"{path}: no function_name/canary parameter (apply the customer stack first)")
    return got["function_name"], Canary(**json.loads(got["canary"]))


def firing(alarms: list[str], call: Callable = aws) -> list[str]:
    if not alarms:
        return []
    res = call(["cloudwatch", "describe-alarms", "--alarm-names", *alarms])
    return [a["AlarmName"] for a in res.get("MetricAlarms", []) + res.get("CompositeAlarms", [])
            if a.get("StateValue") == "ALARM"]


def set_alias(call: Callable, function: str, version: str, weights: dict[str, float]) -> None:
    call(["lambda", "update-alias", "--function-name", function, "--name", "live",
          "--function-version", version,
          "--routing-config", json.dumps({"AdditionalVersionWeights": weights})])


def _wait_updated(call: Callable, function: str) -> None:
    call(["lambda", "wait", "function-updated-v2", "--function-name", function])


def start(r: Rollout, zip_path: Path, call: Callable = aws, all_at_once: bool = False,
          now: Callable[[], float] = time.time) -> None:
    sha = code_sha256(zip_path.read_bytes())
    r.current = call(["lambda", "get-alias", "--function-name", r.function, "--name", "live"])["FunctionVersion"]
    live_sha = call(["lambda", "get-function-configuration", "--function-name", r.function,
                     "--qualifier", r.current])["CodeSha256"]
    if live_sha == sha:
        r.status, r.detail = "already-live", f"live (v{r.current}) already runs {sha[:12]}"
        return
    if hot := firing(r.canary.alarms, call):
        r.status, r.detail = "refused", f"rollback alarm already firing: {', '.join(hot)}"
        return
    call(["lambda", "update-function-code", "--function-name", r.function, "--zip-file", f"fileb://{zip_path}"])
    _wait_updated(call, r.function)
    # Pinned to the digest: PublishVersion fails if $LATEST isn't exactly this zip.
    r.target = call(["lambda", "publish-version", "--function-name", r.function, "--code-sha256", sha,
                     "--description", f"lane 2 {sha[:12]}"])["Version"]
    if all_at_once:
        set_alias(call, r.function, r.target, {})
        r.status, r.detail = "promoted", "all at once"
        r.seconds = round(time.time() - r.started, 1)
        return
    set_alias(call, r.function, r.current, {r.target: r.canary.weight})
    r.status = "canary"
    r.deadline = now() + r.canary.minutes * 60


def check(r: Rollout, call: Callable = aws, now: Callable[[], float] = time.time) -> None:
    """One look at a running canary: roll back on an alarm, promote when the time is up."""
    if hot := firing(r.canary.alarms, call):
        set_alias(call, r.function, r.current, {})
        r.status, r.detail = "rolled-back", f"alarm: {', '.join(hot)}"
    elif now() >= r.deadline:
        set_alias(call, r.function, r.target, {})
        r.status = "promoted"
    else:
        return
    r.seconds = round(time.time() - r.started, 1)


def restore_latest(function: str, call: Callable = aws, fetch: Callable[[str], bytes] | None = None) -> str:
    """After a rollback: publish the code `live` runs as the newest version (same digest),
    so the next Terraform apply can't move `live` onto the bad build."""
    live = call(["lambda", "get-alias", "--function-name", function, "--name", "live"])["FunctionVersion"]
    fn = call(["lambda", "get-function", "--function-name", function, "--qualifier", live])
    sha, url = fn["Configuration"]["CodeSha256"], fn["Code"]["Location"]
    data = (fetch or (lambda u: urllib.request.urlopen(u, timeout=60).read()))(url)  # noqa: S310 (presigned S3 URL)
    if code_sha256(data) != sha:
        raise AwsError(f"downloaded code of v{live} doesn't match its CodeSha256")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(data)
    try:
        call(["lambda", "update-function-code", "--function-name", function, "--zip-file", f"fileb://{f.name}"])
    finally:
        os.unlink(f.name)
    _wait_updated(call, function)
    return call(["lambda", "publish-version", "--function-name", function, "--code-sha256", sha,
                 "--description", f"restore v{live} after rollback"])["Version"]


def rollout(targets: list[tuple[str, Canary]], zip_path: Path, call: Callable = aws, all_at_once: bool = False,
            sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time,
            out=print, fetch: Callable[[str], bytes] | None = None) -> list[Rollout]:
    """Start every function's canary, then watch them together (they run in parallel)."""
    rs = [Rollout(f, c) for f, c in targets]
    try:
        for r in rs:
            start(r, zip_path, call, all_at_once, now)
            out(f"{r.function}: v{r.current} -> v{r.target}: {r.canary.weight:.0%} for {r.canary.minutes:g} min, "
                f"watching {', '.join(r.canary.alarms)}" if r.status == "canary"
                else f"{r.function}: {r.status} ({r.detail})")
        while any(r.status == "canary" for r in rs):
            sleep(POLL_S)
            for r in rs:
                if r.status == "canary":
                    check(r, call, now)
                    if r.status != "canary":
                        out(f"{r.function}: {r.status} after {r.seconds:.0f} s" + (f" ({r.detail})" if r.detail else ""))
    finally:
        # Never leave `live` split: anything still in its canary (an error above) goes back.
        for r in rs:
            if r.status == "canary":
                try:
                    set_alias(call, r.function, r.current, {})
                    r.status, r.detail = "rolled-back", "deploy interrupted"
                except AwsError as exc:
                    r.status, r.detail = "failed", f"interrupted, and the rollback failed too: {exc}"
    for r in rs:
        if r.status == "rolled-back":
            r.restored = restore_latest(r.function, call, fetch)
            out(f"{r.function}: back on v{r.current}; its code re-published as v{r.restored} (newest = live)")
    return rs


def summary(rs: list[Rollout]) -> dict[str, Any]:
    return {"ok": all(r.status in {"promoted", "already-live"} for r in rs),
            "functions": [{"function": r.function, "status": r.status, "from": r.current, "to": r.target,
                           "seconds": r.seconds, "restored": r.restored, "detail": r.detail} for r in rs]}


def main(argv: list[str] | None = None, call: Callable = aws) -> int:
    p = argparse.ArgumentParser(prog="lambda_deploy")
    p.add_argument("--zip", required=True, type=Path)
    p.add_argument("--env", required=True)
    p.add_argument("--customer", action="append", required=True)
    p.add_argument("--all-at-once", action="store_true", help="skip the canary window (alarms still checked first)")
    p.add_argument("--expect-sha256", help="refuse unless the zip's CodeSha256 is this (the attested digest)")
    a = p.parse_args(argv)
    data = a.zip.read_bytes()
    if a.expect_sha256 and code_sha256(data) != a.expect_sha256:
        print(f"refusing: {a.zip} is {code_sha256(data)}, expected {a.expect_sha256}", file=sys.stderr)
        return 2
    targets = [customer_target(c, a.env, call) for c in a.customer]
    s = summary(rollout(targets, a.zip, call, all_at_once=a.all_at_once))
    print(json.dumps(s, indent=1))
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
