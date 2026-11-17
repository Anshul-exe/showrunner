"""Lane 2, app code (ADR 0004): build the zip, update every function of that service in
the env, publish a version, point alias `live` at it. No Terraform involved.

`incident` ships the incident loop's five functions (one bundle). Core simplification:
the alias moves all at once. Tier 2 replaces this with a container
image deployed by digest and a CodeDeploy canary on `live` (Canary10Percent5Minutes,
rolled back by the error/latency alarms).
"""

from __future__ import annotations

import sys
import time

from common import ROOT, client, customer_params, customers, shared_params

sys.path.insert(0, str(ROOT / "tools" / "build"))
import package  # noqa: E402


def functions(service: str, env: str) -> list[str]:
    if service == "catalog-api":
        return [customer_params(c, env)["function_name"] for c in customers(env)]
    if service == "vendor-mock":
        return [shared_params(env)["vendor_function_name"]]
    if service == "incident":  # one bundle, five functions (modules/ops-core)
        return [f"sr-ops-incident-{f}" for f in ("github", "evidence", "triage", "verify", "act")]
    raise SystemExit(f"unknown service {service}")


def deploy(service: str, env: str) -> int:
    zip_path = package.build(service)
    code = zip_path.read_bytes()
    sha = package.source_hash(zip_path)
    lam = client("lambda")
    print(f"{service}: {zip_path.relative_to(ROOT)} sha256={sha}")
    for name in functions(service, env):
        started = time.time()
        current = lam.get_alias(FunctionName=name, Name="live")["FunctionVersion"]
        if lam.get_function_configuration(FunctionName=name, Qualifier=current)["CodeSha256"] == sha:
            print(f"  {name}: live (v{current}) already runs this code, skipping")
            continue
        lam.update_function_code(FunctionName=name, ZipFile=code, Publish=False)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        version = lam.publish_version(FunctionName=name, CodeSha256=sha, Description=f"sr deploy {sha[:12]}")["Version"]
        lam.update_alias(FunctionName=name, Name="live", FunctionVersion=version)
        print(f"  {name}: live v{current} -> v{version} in {time.time() - started:.0f} s (rollback: alias live -> v{current})")
    return 0
