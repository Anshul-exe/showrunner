"""The agents' instruction files are their contract. CI enforces it here."""

import json
from pathlib import Path

import pytest

from incident import agents

ROOT = Path(__file__).resolve().parents[2]
AGENT_FILES = sorted(p for p in (ROOT / "ops" / "agents").glob("*.md") if p.name != "README.md")
REQUIRED = {"name", "purpose", "model", "max_tokens", "temperature", "reads", "never_touches", "may_propose", "iam", "output_schema"}
ALLOWED_MODELS = {"amazon.nova-lite-v1:0"}


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_instruction_file_is_complete_and_least_privilege(path):
    doc = agents.load(path.stem, path.parent)
    assert REQUIRED <= set(doc.meta), REQUIRED - set(doc.meta)
    assert doc.meta["name"] == path.stem
    assert doc.meta["model"] in ALLOWED_MODELS
    assert 0 < doc.meta["max_tokens"] <= 1000  # a cap per call (CLAUDE.md: agent calls have caps)
    # Agents propose; they never act. Model invocation on their own model only.
    for stmt in doc.meta["iam"]:
        assert stmt["actions"] == ["bedrock:InvokeModel"], stmt
        assert stmt["resources"] == [f"arn:aws:bedrock:us-east-1::foundation-model/{doc.meta['model']}"]
    assert len(doc.body) > 200  # the system prompt


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_terraform_builds_the_policy_from_this_file(path):
    tf = (ROOT / "platform" / "modules" / "ops-core" / "agents.tf").read_text()
    assert "ops/agents" in tf and "yamldecode" in tf
    assert f'"{path.stem}"' in tf, f"modules/ops-core/agents.tf doesn't load {path.name}"


class FakeBedrock:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return {"output": {"message": {"content": [{"text": self.text}]}},
                "usage": {"inputTokens": 1000, "outputTokens": 200}}


def test_run_uses_the_file_and_prices_the_call():
    doc = agents.load("triage")
    out = {"hypotheses": [{"component": "vendor-recs", "statement": "slow", "cites": ["E5"], "action": None}]}
    fake = FakeBedrock("Here you go:\n" + json.dumps(out))
    res = agents.run(fake, doc, {"x": 1})
    assert res.output == out and res.input_tokens == 1000
    assert res.cost_usd == round(1 * 0.00006 + 0.2 * 0.00024, 6)
    call = fake.calls[0]
    assert call["modelId"] == doc.meta["model"] and call["system"][0]["text"] == doc.body
    assert call["inferenceConfig"]["maxTokens"] == doc.meta["max_tokens"]


@pytest.mark.parametrize("fake", [
    FakeBedrock(exc=RuntimeError("Operation not allowed")),
    FakeBedrock("no json at all"),
    FakeBedrock('{"hypotheses": [{"component": "kubernetes", "statement": "x", "cites": ["E1"]}]}'),
    FakeBedrock('{"hypotheses": [{"component": "valkey", "statement": "x", "cites": ["file.txt"]}]}'),
])
def test_any_model_problem_means_unavailable(fake):
    with pytest.raises(agents.AgentUnavailable):
        agents.run(fake, agents.load("triage"), {})


def test_validator_subset():
    schema = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "array", "maxItems": 1, "items": {"type": ["string", "null"], "pattern": "^x"}}}}
    assert agents.validate({"a": ["xy"]}, schema) == []
    assert agents.validate({"a": [None]}, schema) == []
    assert agents.validate({}, schema) == ["$: missing a"]
    assert len(agents.validate({"a": ["y", "x"]}, schema)) == 2
