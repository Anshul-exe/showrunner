"""PR review agents: router, evidence, the verifier's checks, caps, the comment, and the
contract between the instruction files and the bootstrap IAM role."""

import json
import re
from pathlib import Path

import pytest

from review import __main__ as cli
from review import agents, core, diff, iam, render

ROOT = Path(__file__).resolve().parents[2]

# A Terraform file after a PR that adds four statements: a real wildcard grant, a Deny
# with wildcards, an action that has no resource type, and a scoped grant.
AFTER = """\
data "aws_iam_policy_document" "evidence" {
  statement {
    sid       = "EnvOutputs"
    actions   = ["ssm:GetParametersByPath"]
    resources = local.env_params
  }

  statement {
    sid       = "NetworkLogs"
    actions   = ["s3:*"]
    resources = ["*"]
  }

  statement {
    sid       = "TaskHeartbeat"
    actions   = ["states:SendTaskHeartbeat"]
    resources = ["*"]
  }

  statement {
    sid       = "DenyOutside"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account]
    }
  }

  statement {
    sid       = "Scoped"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::logs/*"]
  }
}
"""
FILE = "platform/modules/ops-core/main.tf"


def make_diff(after: str, first_added: int = 7) -> str:
    lines = after.splitlines()
    added = lines[first_added - 1:-1]
    return (f"--- a/{FILE}\n+++ b/{FILE}\n@@ -1,6 +1,{len(lines)} @@\n"
            + "\n".join(" " + l for l in lines[:first_added - 1]) + "\n"
            + "\n".join("+" + l for l in added) + "\n " + lines[-1] + "\n")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / FILE).parent.mkdir(parents=True)
    (tmp_path / FILE).write_text(AFTER)
    return tmp_path


@pytest.fixture
def evidence():
    return core.build_evidence(make_diff(AFTER), None, [FILE])


def line_of(text: str) -> int:
    return next(i for i, l in enumerate(AFTER.splitlines(), 1) if text in l)


def finding(fid: str, line: int, category: str = "iam-wildcard", severity: str = "high") -> dict:
    return {"id": fid, "category": category, "severity": severity, "file": FILE, "line": line,
            "title": "wildcard", "claim": "grants too much", "cites": ["E1"]}


class FakeModel:
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request):
        self.requests.append(request)
        text = self.answers.pop(0)
        return {"output": {"message": {"content": [{"text": text if isinstance(text, str) else json.dumps(text)}]}},
                "usage": {"inputTokens": 800, "outputTokens": 150}}


def budget(**kw):
    return agents.Budget(counter=lambda day, cap: None, **kw)


# ------------------------------------------------------------------ diff + router
def test_diff_gives_new_file_line_numbers():
    fd = diff.parse(make_diff(AFTER))[FILE]
    assert min(fd.added) == 7 and line_of('"s3:*"') in fd.added
    assert line_of('sid       = "EnvOutputs"') not in fd.added  # context, not added


def test_router_picks_specialists_by_path():
    routed = core.route([FILE, "README.md", ".github/workflows/ci.yml"])
    assert routed["iac-security"] == [FILE]
    assert routed["supply-chain"] == [".github/workflows/ci.yml"]
    assert "iac-security" not in core.route(["README.md", "services/catalog-api/app.py"])


def test_evidence_keeps_only_scanner_hits_on_changed_lines(evidence):
    report = {"results": {"failed_checks": [
        {"check_id": "CKV_AWS_355", "check_name": "no * resource", "repo_file_path": "/" + FILE,
         "file_line_range": [8, 12], "resource": "data.aws_iam_policy_document.evidence"},
        {"check_id": "CKV_AWS_1", "check_name": "old", "repo_file_path": "/" + FILE,
         "file_line_range": [2, 6], "resource": "x"}]}}  # lines this PR didn't touch
    ev = core.build_evidence(make_diff(AFTER), report, [FILE])
    assert [e["kind"] for e in ev] == ["hunk", "checkov"]
    assert ev[1]["check"] == "CKV_AWS_355"


# ------------------------------------------------------------------ IAM statement reader
def test_statement_reader_handles_allow_deny_and_json():
    st = iam.statement_at(AFTER, line_of('"s3:*"'))
    assert (st.effect, st.actions, st.resources, st.wildcard_action) == ("Allow", ["s3:*"], ["*"], True)
    deny = iam.statement_at(AFTER, line_of('effect    = "Deny"') + 1)
    assert deny.effect == "Deny"
    js = 'policy = jsonencode({\n  Statement = [{\n    Effect   = "Allow"\n    Action   = ["iam:*"]\n    Resource = "*"\n  }]\n})\n'
    st = iam.statement_at(js, 4)
    assert st.actions == ["iam:*"] and st.resources == ["*"] and st.wildcard_resource


# ------------------------------------------------------------------ verifier checks
@pytest.mark.parametrize("text,verdict,check", [
    ('"s3:*"', "confirmed", "grants-access"),
    ('"states:SendTaskHeartbeat"', "refuted", "resource-type"),
    ('effect    = "Deny"', "refuted", "grants-access"),
    ('"s3:GetObject"', "refuted", "wildcard"),
])
def test_verifier_checks(repo, evidence, text, verdict, check):
    checks = core.checks_for(finding("F1", line_of(text)), evidence, repo)
    assert core.verdict_from(checks) == verdict
    assert any(c["name"] == check for c in checks)


def test_finding_on_a_line_the_pr_did_not_change_is_refuted(repo, evidence):
    checks = core.checks_for(finding("F1", line_of('"ssm:GetParametersByPath"')), evidence, repo)
    assert core.verdict_from(checks) == "refuted" and checks[0]["name"] == "in-this-pr"


def test_model_only_sees_what_the_checks_left_open(repo, evidence):
    findings = [finding("F1", line_of('"s3:*"')), finding("F2", line_of('"states:SendTaskHeartbeat"')),
                finding("F3", line_of('"s3:GetObject"') - 1, category="encryption", severity="medium")]
    model = FakeModel({"verdicts": [{"finding": "F3", "verdict": "refuted", "explanation": "line 33 is not a bucket"}]})
    ver = core.verify(findings, evidence, repo, budget(), model)
    sent = json.loads(model.requests[0]["messages"][0]["content"][0]["text"])
    assert [f["id"] for f in sent["findings"]] == ["F3"]
    by = {v["finding"]: v for v in ver["verdicts"]}
    assert (by["F1"]["verdict"], by["F1"]["decided_by"]) == ("confirmed", "checks")
    assert (by["F3"]["verdict"], by["F3"]["decided_by"]) == ("refuted", "model")


def test_no_model_call_when_checks_settle_everything(repo, evidence):
    model = FakeModel()
    ver = core.verify([finding("F1", line_of('"s3:*"'))], evidence, repo, budget(), model)
    assert model.requests == [] and ver["usage"]["mode"] == "checks only"


# ------------------------------------------------------------------ specialist + caps
def test_specialist_drops_uncited_findings(evidence):
    out = {"findings": [
        {"category": "iam-wildcard", "severity": "high", "file": FILE, "line": 10, "title": "s3:*", "claim": "x", "cites": ["E1"]},
        {"category": "other", "severity": "low", "file": FILE, "line": 1, "title": "made up", "claim": "x", "cites": ["E9"]}]}
    spec = core.specialist("iac-security", evidence, budget(), FakeModel(out))
    assert spec["mode"] == "model" and [f["title"] for f in spec["findings"]] == ["s3:*"]
    assert spec["tokens"] == [800, 150] and spec["cost_usd"] == pytest.approx(0.000084)


def test_specialist_falls_back_to_scanner_rules_on_bad_output():
    report = {"results": {"failed_checks": [{"check_id": "CKV_AWS_355", "check_name": "no *", "repo_file_path": FILE,
                                             "file_line_range": [8, 12], "resource": "r"}]}}
    ev = core.build_evidence(make_diff(AFTER), report, [FILE])
    spec = core.specialist("iac-security", ev, budget(), FakeModel("sorry, I can't"))
    assert spec["mode"] == "rules" and "no JSON" in spec["reason"]
    assert [(f["category"], f["cites"]) for f in spec["findings"]] == [("iam-wildcard", ["E2"])]


def test_per_run_cap():
    b = budget(run_cap=2)
    b.take(), b.take()
    with pytest.raises(agents.AgentUnavailable, match="per-run cap of 2"):
        b.take()


def test_daily_cap_and_counter_outage_mean_no_call():
    def full(day, cap):
        raise agents.AgentUnavailable("An error occurred (ConditionalCheckFailedException) when calling UpdateItem")

    def down(day, cap):
        raise agents.AgentUnavailable("AccessDenied")

    with pytest.raises(agents.AgentUnavailable, match="daily cap of 40"):
        agents.Budget(counter=full).take()
    with pytest.raises(agents.AgentUnavailable, match="no model call without it"):
        agents.Budget(counter=down).take()


# ------------------------------------------------------------------ comment + exit code
def test_comment_and_gate(repo, evidence, monkeypatch, capsys, tmp_path):
    out = {"findings": [
        {"category": "iam-wildcard", "severity": "high", "file": FILE, "line": line_of('"s3:*"'), "title": "s3:* on *", "claim": "any bucket", "cites": ["E1"]},
        {"category": "iam-wildcard", "severity": "high", "file": FILE, "line": line_of('"states:SendTaskHeartbeat"'), "title": "states on *", "claim": "x", "cites": ["E1"]}]}
    model = FakeModel(out)
    monkeypatch.setattr(agents, "aws", lambda args, payload=None: model(payload) if args[0] == "bedrock-runtime" else {})
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps(evidence))
    routed = tmp_path / "r.json"
    routed.write_text(json.dumps(core.route([FILE])))
    assert cli.main(["run", "--evidence", str(ev), "--root", str(repo), "--routed", str(routed)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["blocking"] and result["model_calls"] == 1
    body = render.comment(result["routed"], result["specialist"], result["verification"],
                          {n: s["built"] for n, s in core.SPECIALISTS.items()})
    assert body.startswith(render.MARKER)
    assert "**1 confirmed**, 1 refuted" in body
    assert f"`{FILE}:{line_of(chr(34) + 's3:*')}`" in body
    assert "<details><summary>1 finding(s) refuted" in body and "no IAM resource type" in body
    assert "**Check result: failed**" in body and "`cost`" in body  # routed, not built


# ------------------------------------------------------------------ contract
AGENT_FILES = sorted((ROOT / "review" / "agents").glob("*.md"))


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_instruction_files_are_complete(path):
    a = agents.load(path.stem)
    assert {"name", "purpose", "model", "max_tokens", "reads", "never_touches", "may_propose", "iam", "output_schema"} <= set(a.meta)
    assert a.meta["name"] == path.stem and a.meta["model"] == "amazon.nova-lite-v1:0"
    assert 0 < a.meta["max_tokens"] <= 1000 and len(a.prompt) > 300


def _hcl_statements(tf: str, doc: str) -> list[dict]:
    block = re.search(rf'data "aws_iam_policy_document" "{doc}" \{{(.*?)\n\}}', tf, re.S).group(1)
    out = []
    for s in re.findall(r"statement \{(.*?)\n  \}", block, re.S):
        out.append({"actions": re.findall(r'"([a-z0-9-]+:[A-Za-z*]+)"', re.search(r"actions\s*=\s*\[(.*?)\]", s, re.S).group(1)),
                    "resources": re.search(r"resources\s*=\s*\[(.*?)\]", s, re.S).group(1)})
    return out


def test_bootstrap_role_matches_instruction_files():
    """The review-agents role = the agents' `iam` + the call counter, nothing else."""
    tf = (ROOT / "platform" / "bootstrap" / "roles.tf").read_text()
    stmts = _hcl_statements(tf, "review_agents")
    declared = {a for p in AGENT_FILES for s in agents.load(p.stem).meta["iam"] for a in s["actions"]}
    counter = {"dynamodb:UpdateItem", "dynamodb:GetItem"}
    granted = {a for s in stmts for a in s["actions"]}
    assert granted == declared | counter
    for s in stmts:
        if set(s["actions"]) & counter:
            assert set(s["actions"]) <= counter and "ops-agent-budget" in s["resources"]
        else:
            assert "foundation-model/${var.bedrock_model_id}" in s["resources"]
    variables = (ROOT / "platform" / "bootstrap" / "variables.tf").read_text()
    model = re.search(r'variable "bedrock_model_id".*?default\s*=\s*"([^"]+)"', variables, re.S).group(1)
    assert {agents.load(p.stem).meta["model"] for p in AGENT_FILES} == {model}
    assert agents.BUDGET_TABLE == "sr-ops-agent-budget"


def test_structured_output_through_a_forced_tool_call(evidence):
    """The answer is read from the forced tool call; the schema is still validated."""
    good = {"findings": [{"category": "iam-wildcard", "severity": "high", "file": FILE, "line": 10,
                          "title": "s3:*", "claim": "x", "cites": ["E1"]}]}
    bad = {"findings": [dict(good["findings"][0], severy="high")]}
    del bad["findings"][0]["severity"]  # the misspelling Nova Lite produced on 18/11/2026

    def tool_answer(payload):
        def call(request):
            call.request = request
            return {"output": {"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": "report", "input": payload}}]}},
                    "usage": {"inputTokens": 1, "outputTokens": 1}}
        return call

    call = tool_answer(good)
    spec = core.specialist("iac-security", evidence, budget(), call)
    assert spec["mode"] == "model" and len(spec["findings"]) == 1
    tc = call.request["toolConfig"]
    assert tc["toolChoice"] == {"tool": {"name": "report"}}
    assert tc["tools"][0]["toolSpec"]["inputSchema"]["json"] == agents.load("iac-security").meta["output_schema"]
    spec = core.specialist("iac-security", evidence, budget(), tool_answer(bad))
    assert spec["mode"] == "rules" and "missing severity" in spec["reason"]
