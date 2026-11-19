"""Instruction files, model calls (via the AWS CLI) and the call caps.

Instruction file = `review/agents/<name>.md`: JSON front matter between `---` lines (JSON
is valid YAML, and the standard library reads it), then the system prompt. The file's
SHA-256 goes into the PR comment, so each finding traces to the exact instructions.

Caps: at most MAX_CALLS_PER_RUN model calls per review, and a per-day counter in
DynamoDB (`sr-ops-agent-budget`, conditional increment). No model call without the
counter. Every reason the model isn't used has a kind (AgentUnavailable.kind): only the
daily budget is a designed limit, where the rules-mode result stands; any other kind
(counter table, Bedrock, throttling, an answer that fails its schema) means the PR
wasn't reviewed, and the gate fails closed (ADR 0013).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
MAX_CALLS_PER_RUN = 2
DAILY_CAP = 40
BUDGET_TABLE = "sr-ops-agent-budget"
REGION = os.environ.get("AWS_REGION", "us-east-1")
TOOL = "report"
PRICES = {"amazon.nova-lite-v1:0": (0.00006, 0.00024)}  # USD per 1K tokens (docs/COST.md)


class AgentUnavailable(Exception):
    """kind: "budget" (daily cap reached: a designed limit), "infra" (counter table,
    Bedrock, throttling, credentials), "output" (the answer failed its schema), "cap"
    (per-run cap: only a code bug can reach it)."""

    def __init__(self, message: str, kind: str = "infra"):
        super().__init__(message)
        self.kind = kind


DESIGNED_LIMITS = {"budget"}  # the only fallbacks after which a rules-mode result stands


@dataclass
class Agent:
    name: str
    meta: dict[str, Any]
    prompt: str
    sha256: str


def load(name: str, directory: Path = AGENTS_DIR) -> Agent:
    raw = (directory / f"{name}.md").read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    if not m:
        raise ValueError(f"{name}.md: no front matter")
    return Agent(name, json.loads(m.group(1)), m.group(2).strip(), hashlib.sha256(raw.encode()).hexdigest())


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """The JSON Schema subset the instruction files use (same as ops/incident)."""
    errors: list[str] = []
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        pytype = {"object": dict, "array": list, "string": str, "null": type(None),
                  "integer": int, "number": (int, float), "boolean": bool}
        if not any(isinstance(value, pytype[t]) for t in types):
            return [f"{path}: expected {types}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: doesn't match {schema['pattern']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items") or {}, f"{path}[{i}]")
    return errors


def extract_json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AgentUnavailable("no JSON object in the model's answer", "output")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AgentUnavailable(f"invalid JSON from the model: {exc.msg}", "output") from None


# ------------------------------------------------------------------ AWS CLI
def aws(args: list[str], payload: dict | None = None) -> dict:
    """Run the AWS CLI (preinstalled on GitHub runners) and return its JSON output."""
    cmd = ["aws", *args, "--region", REGION, "--output", "json"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        if payload is not None:
            json.dump(payload, f)
            cmd += ["--cli-input-json", f"file://{f.name}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "AWS_MAX_ATTEMPTS": "1"})  # a retry would be a second call
    finally:
        os.unlink(f.name)
    if r.returncode != 0:
        raise AgentUnavailable(r.stderr.strip().splitlines()[-1][:200] if r.stderr.strip() else f"aws exit {r.returncode}")
    return json.loads(r.stdout or "{}")


class Budget:
    """Per-run and per-day caps on model calls."""

    def __init__(self, run_cap: int = MAX_CALLS_PER_RUN, daily_cap: int = DAILY_CAP,
                 counter: Callable[[str, int], None] | None = None):
        self.run_cap, self.daily_cap, self.used = run_cap, daily_cap, 0
        self.counter = counter if counter is not None else self._dynamodb_counter
        self.note = ""

    def _dynamodb_counter(self, day: str, cap: int) -> None:
        aws(["dynamodb", "update-item"], {
            "TableName": BUDGET_TABLE, "Key": {"pk": {"S": f"review#{day}"}},
            "UpdateExpression": "ADD calls :one SET expires_at = :exp",
            "ConditionExpression": "attribute_not_exists(calls) OR calls < :cap",
            "ExpressionAttributeValues": {":one": {"N": "1"}, ":cap": {"N": str(cap)},
                                          ":exp": {"N": str(int(datetime.now(timezone.utc).timestamp()) + 7 * 86400)}},
        })

    def take(self) -> None:
        if self.used >= self.run_cap:
            raise AgentUnavailable(f"per-run cap of {self.run_cap} model calls reached", "cap")
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            self.counter(day, self.daily_cap)
        except AgentUnavailable as exc:
            if "ConditionalCheckFailed" in str(exc):
                raise AgentUnavailable(f"daily cap of {self.daily_cap} review calls reached", "budget") from None
            raise AgentUnavailable(f"call counter unavailable ({exc}); no model call without it") from None
        self.used += 1


@dataclass
class Result:
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float


def run(agent: Agent, payload: dict, budget: Budget, call: Callable[[dict], dict] | None = None) -> Result:
    budget.take()
    request = {
        "modelId": agent.meta["model"],
        "system": [{"text": agent.prompt}],
        "messages": [{"role": "user", "content": [{"text": json.dumps(payload, default=str)}]}],
        "inferenceConfig": {"maxTokens": int(agent.meta["max_tokens"]),
                            "temperature": float(agent.meta.get("temperature", 0))},
        # Structured output: the answer is the input of a forced tool call, constrained by
        # the instruction file's schema. (Plain-text JSON came back with a misspelt key,
        # "severy", on 18/11/2026.) The schema is still validated below.
        "toolConfig": {"tools": [{"toolSpec": {"name": TOOL, "description": "Report the result.",
                                               "inputSchema": {"json": agent.meta["output_schema"]}}}],
                       "toolChoice": {"tool": {"name": TOOL}}},
    }
    resp = (call or (lambda req: aws(["bedrock-runtime", "converse"], req)))(request)
    content = resp["output"]["message"]["content"]
    tool = next((c["toolUse"] for c in content if "toolUse" in c), None)
    text = "".join(c.get("text", "") for c in content)
    if os.environ.get("REVIEW_DEBUG"):  # local runs only: the raw answer, to stderr
        print(f"--- {agent.name} raw answer ---\n{json.dumps(tool) if tool else text}", file=sys.stderr)
    output = tool["input"] if tool else extract_json(text)
    problems = validate(output, agent.meta["output_schema"])
    if problems:
        raise AgentUnavailable("output failed its schema: " + "; ".join(problems[:3]), "output")
    usage = resp.get("usage", {})
    tin, tout = int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
    pin, pout = PRICES.get(agent.meta["model"], (0.0, 0.0))
    return Result(output, tin, tout, round(tin / 1000 * pin + tout / 1000 * pout, 6))
