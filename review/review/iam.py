"""Read the IAM policy statement around a line of Terraform, well enough to verify an
`iam-wildcard` finding with code instead of opinion.

Handles the two shapes this repo uses: `statement { ... }` blocks in
`aws_iam_policy_document`, and `jsonencode({ Statement = [{ ... }] })` objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Actions with no resource type in the Service Authorization Reference: `Resource: "*"`
# is the only valid form, so a wildcard there is not a finding (ADR 0008).
# Exact action names only, never patterns: `ec2:Describe*` would also cover actions added
# later, unchecked. Each entry was checked against the reference's "Resource types"
# column (machine-readable copy: https://servicereference.us-east-1.amazonaws.com/).
# An action that has a resource type (e.g. ec2:GetManagedPrefixListEntries: prefix-list,
# logs:GetQueryResults: log-group) never goes here: scope it instead.
NO_RESOURCE_TYPE = {
    "states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat",
    "sts:GetCallerIdentity", "ecr:GetAuthorizationToken", "cloudwatch:PutMetricData",
    "cloudwatch:GetMetricData", "cloudwatch:ListMetrics", "xray:PutTraceSegments",
    "xray:PutTelemetryRecords", "ec2:DescribeNetworkInterfaces", "ec2:CreateNetworkInterface",
    "ec2:DeleteNetworkInterface", "logs:DescribeLogGroups", "ssm:DescribeParameters",
    "appconfig:ListApplications", "cloudtrail:LookupEvents", "elasticache:DescribeServerlessCaches",
    # The incident loop's network evidence (ADR 0014), checked 19/11/2026 against
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonec2.html
    "ec2:DescribeSecurityGroups", "ec2:DescribeNetworkInsightsAnalyses", "ec2:DescribeNetworkInsightsPaths",
    "ec2:DescribeSubnets", "ec2:DescribeRouteTables", "ec2:DescribeNetworkAcls", "ec2:DescribeVpcs",
    "ec2:DescribeVpcEndpoints", "ec2:DescribeVpcEndpointServiceConfigurations", "ec2:DescribeInstances",
    "ec2:DescribeInternetGateways", "ec2:DescribeNatGateways", "ec2:DescribeAddresses",
    "ec2:DescribePrefixLists", "ec2:DescribeManagedPrefixLists", "ec2:DescribeVpcPeeringConnections",
    "ec2:DescribeAvailabilityZones", "ec2:DescribeRegions",
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonvpcreachabilityanalyzer.html
    "tiros:CreateQuery", "tiros:GetQueryAnswer", "tiros:GetQueryExplanation",
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatchlogs.html
    "logs:StopQuery",
}


@dataclass
class Statement:
    start: int  # 1-based line numbers in the file
    end: int
    effect: str
    actions: list[str]
    not_actions: list[str]
    resources: list[str]
    not_resources: list[str]

    @property
    def wildcard_action(self) -> bool:
        # any pattern counts (`s3:*`, `ec2:Get*`, `iam:*Policy`): it covers actions nobody checked
        return any("*" in a or "?" in a for a in self.actions) or bool(self.not_actions)

    @property
    def wildcard_resource(self) -> bool:
        return "*" in self.resources or bool(self.not_resources)


def _block_bounds(lines: list[str], line: int) -> tuple[int, int] | None:
    """The innermost `statement {` block or `{ ... Effect/Action ... }` object containing
    `line` (1-based), as (start, end) line numbers."""
    idx = line - 1
    for i in range(min(idx, len(lines) - 1), -1, -1):
        s = lines[i]
        if re.match(r"\s*statement\s*\{", s) or (re.search(r"\{\s*$", s) and _is_json_statement(lines, i)):
            depth, j = 0, i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0 and j > i:
                    break
                if depth <= 0 and j == i and "}" in lines[j]:
                    break
                j += 1
            if i <= idx <= j:
                return i + 1, j + 1
    return None


def _is_json_statement(lines: list[str], i: int) -> bool:
    window = "\n".join(lines[i:i + 8])
    return bool(re.search(r"\b(Effect|Action)\s*=", window))


def _list(block: str, *keys: str) -> list[str]:
    for key in keys:
        m = re.search(rf"\b{key}\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", block, re.S)
        if m:
            return re.findall(r'"([^"]*)"', m.group(1))
    return []


def statement_at(text: str, line: int) -> Statement | None:
    lines = text.splitlines()
    bounds = _block_bounds(lines, line)
    if not bounds:
        return None
    a, b = bounds
    block = "\n".join(lines[a - 1:b])
    effect = (re.search(r"\b(?:effect|Effect)\s*=\s*\"(\w+)\"", block) or [None, "Allow"])[1]
    return Statement(
        a, b, effect,
        _list(block, "actions", "Action"), _list(block, "not_actions", "NotAction"),
        _list(block, "resources", "Resource"), _list(block, "not_resources", "NotResource"),
    )
