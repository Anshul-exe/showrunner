"""Run an agent from its instruction file (ops/agents/<name>.md) on Bedrock.

Model, limits, output schema and system prompt all come from the file; its SHA-256 goes
to the audit trail. Any failure (Bedrock unavailable, throttled, invalid JSON, schema
violation) raises AgentUnavailable, and the caller uses the deterministic path instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opslib import frontmatter

def bundled(name: str) -> Path:
    """ops/<name> in the repo; <name>/ next to the package inside the Lambda zip."""
    here = Path(__file__).resolve().parent.parent
    return here / name if (here / name).is_dir() else here.parent / name


AGENTS_DIR = bundled("agents")
# USD per 1,000 tokens, us-east-1 on-demand (checked 18/11/2026, docs/COST.md).
PRICES = {"amazon.nova-lite-v1:0": (0.00006, 0.00024)}


class AgentUnavailable(Exception):
    pass


@dataclass
class Result:
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float


def load(name: str, directory: Path = AGENTS_DIR) -> frontmatter.Document:
    return frontmatter.load(directory / f"{name}.md")


# ------------------------------------------------------------------ minimal JSON Schema
def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """The subset of JSON Schema the instruction files use (no extra dependency)."""
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
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items") or {}, f"{path}[{i}]")
    return errors


def extract_json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AgentUnavailable("no JSON object in the model's answer")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentUnavailable(f"invalid JSON from the model: {exc.msg}") from None


def run(bedrock, doc: frontmatter.Document, payload: dict[str, Any]) -> Result:
    meta = doc.meta
    try:
        resp = bedrock.converse(
            modelId=meta["model"],
            system=[{"text": doc.body}],
            messages=[{"role": "user", "content": [{"text": json.dumps(payload, default=str)}]}],
            inferenceConfig={"maxTokens": int(meta["max_tokens"]), "temperature": float(meta.get("temperature", 0))},
        )
    except Exception as exc:
        raise AgentUnavailable(f"{type(exc).__name__}: {str(exc)[:160]}") from None
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    output = extract_json(text)
    problems = validate(output, meta["output_schema"])
    if problems:
        raise AgentUnavailable("output failed its schema: " + "; ".join(problems[:3]))
    usage = resp.get("usage", {})
    tin, tout = int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
    pin, pout = PRICES.get(meta["model"], (0.0, 0.0))
    return Result(output, tin, tout, round(tin / 1000 * pin + tout / 1000 * pout, 6))
