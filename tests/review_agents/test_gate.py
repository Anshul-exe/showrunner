"""The review gate: fails closed, never skipped, and records accept-risk overrides."""

import json
from pathlib import Path

import pytest
import yaml

from review import __main__ as cli
from review import agents, gate, render

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "review-agents.yml"
HIGH = [{"id": "F1", "severity": "high", "file": "platform/modules/ops-core/main.tf", "line": 228, "title": "s3:* on *",
         "category": "iam-wildcard", "claim": "x", "cites": ["E1"]}]
LABELED = {"event": "labeled", "label": {"name": "accept-risk"}, "actor": {"login": "owner"},
           "created_at": "2026-11-19T09:00:00Z"}
BOT = {"user": {"login": "github-actions[bot]"}, "body": render.MARKER + "\n## Review agents",
       "created_at": "2026-11-19T09:01:00Z", "html_url": "https://github.com/o/r/pull/12#issuecomment-1"}
REASON = {"user": {"login": "owner"}, "body": "Accepted: the bucket list is read-only in practice; narrowing in #13.",
          "created_at": "2026-11-19T09:02:00Z", "html_url": "https://github.com/o/r/pull/12#issuecomment-2"}


def run(**kw):
    base = dict(fork=False, evidence_result="success", review_needed=True, review_result="success",
                blocking=True, labels=[], events=[], comments=[], highs=HIGH, repo="o/r", pr=12,
                run_url="https://github.com/o/r/actions/runs/1")
    return gate.decide(**{**base, **kw})


# ------------------------------------------------------------------ fail closed
def test_fork_pr_is_not_reviewed_and_fails():
    d = run(fork=True, evidence_result="skipped", review_needed=False, review_result="skipped", blocking=False)
    assert not d.ok and d.message.startswith("not reviewed automatically: fork PR") and not d.comment


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_evidence_job_not_successful_fails_the_gate(result):
    d = run(evidence_result=result, review_needed=False, review_result="skipped", blocking=False)
    assert not d.ok and "failing closed" in d.message
    assert d.comment == (result != "cancelled")  # a superseded run doesn't overwrite the newer comment


def test_review_job_failure_fails_the_gate():
    d = run(review_result="failure", blocking=False)
    assert not d.ok and "review job" in d.message


def test_no_terraform_passes_and_clean_review_passes():
    assert run(review_needed=False, review_result="skipped", blocking=False).ok
    assert run(blocking=False).ok


# ------------------------------------------------------------------ accept-risk
def test_confirmed_high_without_label_fails():
    d = run()
    assert not d.ok and "accept-risk" in d.message and d.record is None


def test_label_without_a_reason_fails():
    old = dict(REASON, created_at="2026-11-19T07:00:00Z")  # more than an hour before the label
    other = dict(REASON, user={"login": "someone-else"})
    d = run(labels=["accept-risk"], events=[LABELED], comments=[BOT, old, other])
    assert not d.ok and "no reason" in d.message


def test_label_with_reason_passes_and_builds_the_audit_row():
    d = run(labels=["accept-risk"], events=[LABELED], comments=[BOT, REASON])
    assert d.ok and d.record
    r = d.record
    assert (r["pk"], r["sk"]) == ("REVIEW#pr-12", "2026-11-19T09:00:00Z#accept-risk")
    assert r["by"] == "owner" and r["at"] == LABELED["created_at"]
    assert r["pr"] == "https://github.com/o/r/pull/12" and r["reason_comment"] == REASON["html_url"]
    assert r["findings"] == [{"id": "F1", "severity": "high", "file": HIGH[0]["file"], "line": 228, "title": "s3:* on *"}]
    req = gate.put_request(r)
    assert req["TableName"] == "sr-ops-audit" and req["ConditionExpression"] == "attribute_not_exists(pk)"
    assert req["Item"]["pk"] == {"S": "REVIEW#pr-12"} and "L" in req["Item"]["findings"]


def test_record_never_overwrites_and_a_rerun_is_fine():
    item = run(labels=["accept-risk"], events=[LABELED], comments=[REASON]).record
    seen = []

    def first(args, payload):
        seen.append(payload)
        return {}

    def again(args, payload):
        raise agents.AgentUnavailable("An error occurred (ConditionalCheckFailedException) when calling the PutItem operation")

    assert gate.record(item, first).startswith("recorded")
    assert seen[0]["ConditionExpression"] == "attribute_not_exists(pk)"
    assert gate.record(item, again).startswith("already recorded")


# ------------------------------------------------------------------ fixture run of the CLI
def test_fixture_run_failed_evidence_fails_the_gate(tmp_path, capsys):
    """What the gate step does when the evidence job crashed: decision.ok false, and a
    'Not reviewed' comment replaces any older 'passed' comment."""
    out = tmp_path / "gate"
    assert cli.main(["gate", "--fork", "false", "--evidence-result", "failure", "--review-needed", "",
                     "--review-result", "skipped", "--blocking", "", "--repo", "o/r", "--pr", "12",
                     "--run-url", "https://github.com/o/r/actions/runs/1", "--out", str(out)]) == 0
    d = json.loads((out / "decision.json").read_text())
    assert d["ok"] is False and d["comment"] is True
    body = (out / "comment.md").read_text()
    assert body.startswith(render.MARKER) and "Not reviewed" in body and "**Check result: failed.**" in body
    assert capsys.readouterr().out.startswith("FAIL: not reviewed")


def test_fixture_run_override_then_record(tmp_path, monkeypatch):
    for name, data in (("labels", [{"name": "accept-risk"}]), ("events", [LABELED]),
                       ("comments", [BOT, REASON]), ("highs", HIGH)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    (tmp_path / "review.md").write_text(render.MARKER + "\n## Review agents\n\n**Check result: failed** (a confirmed high).\n")
    out = tmp_path / "gate"
    args = ["gate", "--fork", "false", "--evidence-result", "success", "--review-needed", "true",
            "--review-result", "success", "--blocking", "true", "--repo", "o/r", "--pr", "12",
            "--run-url", "https://github.com/o/r/actions/runs/1", "--out", str(out), "--review-comment", str(tmp_path / "review.md")]
    for name in ("labels", "events", "comments", "highs"):
        args += [f"--{name}", str(tmp_path / f"{name}.json")]
    cli.main(args)
    assert json.loads((out / "decision.json").read_text())["ok"] is True
    monkeypatch.setattr(agents, "aws", lambda args, payload=None: {})
    assert cli.main(["gate-record", "--dir", str(out)]) == 0
    body = (out / "comment.md").read_text()
    assert "**Override:** `accept-risk` added by owner" in body and "recorded in sr-ops-audit (REVIEW#pr-12" in body


# ------------------------------------------------------------------ the workflow itself
def test_gate_job_always_runs_and_needs_everything():
    wf = yaml.safe_load(WORKFLOW.read_text())
    g = wf["jobs"]["gate"]
    assert g["if"] == "always()"  # a skipped required check counts as passing
    assert set(g["needs"]) == {"evidence", "review"}
    assert g["name"] == "review-agents gate"
    steps = "\n".join(s.get("run", "") for s in g["steps"])
    assert "--evidence-result" in steps and "jq -e .ok" in steps
    # the AWS step only runs for an accepted override
    aws = [s for s in g["steps"] if "configure-aws-credentials" in s.get("uses", "")]
    assert len(aws) == 1 and "record" in aws[0]["if"]
