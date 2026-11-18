"""Can the role that will APPLY this stack read what the stack's plan just read?

A PR plan runs as showrunner-ci-plan (ReadOnlyAccess), but the apply on main runs as
showrunner-ci-apply-<env>, which has denies the plan role doesn't. So a stack could
plan fine in the PR and fail at apply time: that's what happened to ops-core on
18/11/2026 (it reads two dev parameters; the prod apply role denied anything tagged
env=dev). ADR 0010.

This takes the SSM reads out of the plan (data sources, in `prior_state`) and asks IAM's
policy simulator whether the apply role may make each call. The simulator evaluates
the role's real policies and its permissions boundary. It doesn't look up resource
tags by itself, so each parameter's tags are passed in as `aws:ResourceTag/*`
context (that's the condition the 18/11 deny used).

    uv run python tools/ci/access_check.py <stack> <plan.json>   # exit 1 on a deny

Prints a Markdown summary for the plan comment. Reads names and ARNs only, never
values (the plan JSON holds variables in clear; nothing from it is printed but those).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

CI_PREFIX = "showrunner-ci"


@dataclass(frozen=True)
class Read:
    address: str
    action: str
    arn: str
    name: str | None  # parameter name (tags are looked up by it); None for a path


def apply_role(stack: str) -> str:
    """Which role applies this stack on main (see .github/workflows/apply.yml)."""
    if stack == "ops-core" or stack.endswith("/prod"):
        return f"{CI_PREFIX}-apply-prod"
    if stack.endswith("/dev"):
        return f"{CI_PREFIX}-apply-dev"
    raise ValueError(f"no CI apply role for {stack!r} (bootstrap is applied by hand)")


def _data_resources(module: dict):
    for r in module.get("resources", []):
        if r.get("mode") == "data":
            yield r
    for child in module.get("child_modules", []):
        yield from _data_resources(child)


def ssm_reads(plan: dict, region: str, account: str) -> list[Read]:
    """Every SSM read the plan made, as (action, resource ARN)."""
    root = plan.get("prior_state", {}).get("values", {}).get("root_module", {})
    reads: list[Read] = []
    for r in _data_resources(root):
        v = r.get("values") or {}
        if r["type"] == "aws_ssm_parameter":
            reads.append(Read(r["address"], "ssm:GetParameter", v["arn"], v["name"]))
        elif r["type"] == "aws_ssm_parameters_by_path":
            path = "/" + v["path"].strip("/")
            arn = f"arn:aws:ssm:{region}:{account}:parameter{path}"
            reads.append(Read(r["address"], "ssm:GetParametersByPath", arn, None))
    return sorted(set(reads), key=lambda x: x.address)


def tag_context(ssm, name: str | None) -> list[dict]:
    if name is None:
        return []
    tags = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)["TagList"]
    return [
        {"ContextKeyName": f"aws:ResourceTag/{t['Key']}", "ContextKeyValues": [t["Value"]], "ContextKeyType": "string"}
        for t in tags
    ]


def check(reads: list[Read], role_arn: str, iam, ssm) -> list[tuple[Read, str]]:
    """[(read, decision)] where decision is allowed / explicitDeny / implicitDeny."""
    out = []
    for rd in reads:
        res = iam.simulate_principal_policy(
            PolicySourceArn=role_arn,
            ActionNames=[rd.action],
            ResourceArns=[rd.arn],
            ContextEntries=tag_context(ssm, rd.name),
        )
        out.append((rd, res["EvaluationResults"][0]["EvalDecision"]))
    return out


def render(role: str, results: list[tuple[Read, str]]) -> str:
    denied = [(r, d) for r, d in results if d != "allowed"]
    if not results:
        return f"Apply-role access (`{role}`): no SSM reads to check\n"
    if not denied:
        return f"Apply-role access (`{role}`): passed ({len(results)} SSM reads allowed)\n"
    lines = [f"Apply-role access (`{role}`): **FAILED**: this plan works here but the apply on main would fail:"]
    lines += [f"- `{r.action}` on `{r.arn}` ({r.address}): **{d}**" for r, d in denied]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    import boto3

    stack, plan_file = argv[1], argv[2]
    with open(plan_file) as f:
        plan = json.load(f)
    session = boto3.session.Session()
    region = session.region_name or "us-east-1"
    account = session.client("sts").get_caller_identity()["Account"]
    role = apply_role(stack)
    results = check(
        ssm_reads(plan, region, account),
        f"arn:aws:iam::{account}:role/{role}",
        session.client("iam"),
        session.client("ssm", region_name=region),
    )
    print(render(role, results), end="")
    return 0 if all(d == "allowed" for _, d in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
