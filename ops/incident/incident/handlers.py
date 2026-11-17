"""Lambda entry points. Each function has its own IAM role (modules/ops-core):

    github    open the record, post analysis/proposal, notes, escalations, the PIR
    evidence  read-only: metrics, AppConfig, Lambda versions, CloudTrail; recovery check
    triage    Bedrock only (ops/agents/triage.md)
    verify    Bedrock only (ops/agents/verifier.md)
    act       AppConfig deployments for known customer apps, only after /approve

The state machine passes one JSON state between them: {alarm, incident, evidence,
triage, verification, proposal, approval, act, check}.
"""

from __future__ import annotations

import functools
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

from opslib import appconfig_change, runbooks

from . import agents, analysis, evidence, render
from .audit import Audit

RUNBOOKS_DIR = agents.bundled("runbooks")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@functools.cache
def _client(name: str):
    return boto3.client(name, region_name=REGION)


@functools.cache
def _audit() -> Audit:
    return Audit(boto3.resource("dynamodb", region_name=REGION).Table(os.environ["AUDIT_TABLE"]))


@functools.cache
def _runbooks():
    return runbooks.load_all(RUNBOOKS_DIR)


def _params(path: str) -> dict[str, str]:
    out = {}
    for page in _client("ssm").get_paginator("get_parameters_by_path").paginate(Path=path):
        out.update({p["Name"].rsplit("/", 1)[1]: p["Value"] for p in page["Parameters"]})
    return out


def _customer(alarm: dict) -> tuple[dict, dict]:
    return (_params(f"/showrunner/{alarm['env']}/customers/{alarm['customer']}"),
            _params(f"/showrunner/{alarm['env']}/shared"))


@functools.cache
def _github():
    from .github_app import GitHubApp  # cryptography only loads in the github function

    ssm = _client("ssm")
    prefix = os.environ["GITHUB_PARAM_PREFIX"]
    key = ssm.get_parameter(Name=os.environ["GITHUB_KEY_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    app_id = ssm.get_parameter(Name=f"{prefix}/app-id")["Parameter"]["Value"]
    inst = ssm.get_parameter(Name=f"{prefix}/installation-id")["Parameter"]["Value"]
    return GitHubApp(app_id, inst, key, os.environ["GITHUB_REPO"])


def _actions(alarm: dict) -> list[dict]:
    return runbooks.safe_actions(_runbooks()[alarm["kind"]])


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


# ====================================================================== github
def parse_alarm(event: dict) -> dict | None:
    d = event.get("detail", {})
    ref = runbooks.parse_alarm_name(d.get("alarmName", ""), set(_runbooks()))
    if not ref or d.get("state", {}).get("value") != "ALARM":
        return None
    rb = _runbooks()[ref.kind]
    ts = d["state"].get("timestamp", event.get("time", ""))
    return {"name": ref.name, "customer": ref.customer, "env": ref.env, "kind": ref.kind,
            "severity": rb.meta.get("severity", "sev3"), "reason": d["state"].get("reason", "")[:500],
            "time": ts[:19].replace("T", " ") if "T" in ts else ts,
            "iso": ts, "runbook_url": f"{os.environ['RUNBOOK_BASE_URL']}/{ref.kind}.md"}


def github(event: dict, _ctx=None) -> dict:
    step = event["step"]
    t0 = time.time()
    if step == "open":
        return _open(event["event"])
    state = event["state"]
    inc, alarm, gh, audit = state["incident"], state["alarm"], _github(), _audit()
    if step in ("propose", "analysis"):
        token = event.get("taskToken") if step == "propose" else None
        body = render.analysis(state["evidence"], state["triage"], state["verification"], state.get("proposal"),
                               _flags(state["evidence"]), token, int(os.environ.get("APPROVAL_TIMEOUT_MIN", "30")))
        url = gh.comment(inc["issue"], body)
        audit.record(inc["id"], "propose" if token else "analysis", detail=f"posted {url}",
                     output={"proposal": (state.get("proposal") or {}).get("action_id")}, duration_ms=_ms(t0))
        return state
    if step == "note":
        gh.comment(inc["issue"], event["text"])
        audit.record(inc["id"], "note", detail=event["text"][:300], duration_ms=_ms(t0))
        return state
    if step == "escalate":
        reason = event["reason"]
        gh.comment(inc["issue"], f"**Escalation:** {reason} A human needs to take this incident. Issue stays open.")
        gh.add_labels(inc["issue"], ["escalated"])
        topic = os.environ.get("ALERTS_TOPIC_ARN")
        if topic:
            _client("sns").publish(TopicArn=topic, Subject=f"Showrunner escalation: {render.title(alarm)}"[:100],
                                   Message=f"{reason}\n\nIncident record: {inc['url']}")
        audit.record(inc["id"], "escalate", detail=reason, duration_ms=_ms(t0))
        return {**state, "escalated": reason}
    if step == "pir":
        hist = _client("cloudwatch").describe_alarm_history(
            AlarmName=alarm["name"], HistoryItemType="StateUpdate", MaxRecords=100,
            StartDate=datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=30))
        fired = sum(1 for h in hist["AlarmHistoryItems"] if "to ALARM" in h.get("HistorySummary", ""))
        audit.record(inc["id"], "pir", detail="post-incident review posted; issue closed", duration_ms=_ms(t0))
        gh.comment(inc["issue"], render.pir(alarm, inc["id"], audit.rows(inc["id"]), fired))
        if fired >= 3:
            gh.add_labels(inc["issue"], ["problem-management"])
        gh.close(inc["issue"])
        audit.close(alarm["customer"], alarm["env"], inc["id"])
        return state
    raise ValueError(f"unknown step {step}")


def _flags(ev: list[dict]) -> dict | None:
    item = next((e for e in ev if e["kind"] == "runtime_flags"), None)
    return item["data"]["flags"] if item else None


def _open(event: dict) -> dict:
    t0 = time.time()
    alarm = parse_alarm(event)
    if not alarm:
        return {"incident": {"skip": True}}
    audit, gh = _audit(), _github()
    existing = audit.open_incident(alarm["customer"], alarm["env"])
    if existing:
        gh.comment(int(existing["issue"]), render.joined(alarm))
        audit.touch(alarm["customer"], alarm["env"])
        audit.record(existing["incident_id"], "join", detail=f"{alarm['name']} joined", duration_ms=_ms(t0))
        return {"alarm": alarm, "incident": {"id": existing["incident_id"], "issue": int(existing["issue"]),
                                             "url": existing["url"], "reused": True}}
    incident_id = f"{alarm['customer']}-{alarm['env']}-{alarm['iso'][:16].replace(':', '').replace('-', '')}"
    number, url = gh.create_issue(render.title(alarm), render.issue_body(alarm, incident_id),
                                  ["incident", alarm["severity"], f"customer:{alarm['customer']}"])
    if not audit.claim(alarm["customer"], alarm["env"], incident_id, number, url):
        # Another alarm opened the incident a moment earlier: point this issue at it.
        other = audit.open_incident(alarm["customer"], alarm["env"])
        gh.comment(number, f"Duplicate of #{other['issue'] if other else '?'} (opened concurrently).")
        gh.close(number)
        return {"alarm": alarm, "incident": {"skip": True}}
    audit.record(incident_id, "open", detail=f"issue #{number} for {alarm['name']}", output=alarm, duration_ms=_ms(t0))
    return {"alarm": alarm, "incident": {"id": incident_id, "issue": number, "url": url, "reused": False}}


# ==================================================================== evidence
def evidence_fn(event: dict, _ctx=None) -> dict:
    state, t0 = event["state"], time.time()
    alarm, inc = state["alarm"], state["incident"]
    params, shared = _customer(alarm)
    if event["step"] == "collect":
        clients = evidence.Clients(_client("cloudwatch"), _client("appconfig"), _client("lambda"), _client("cloudtrail"))
        items = evidence.collect(clients, alarm, params, shared)
        _audit().record(inc["id"], "evidence", detail=f"{len(items)} items", evidence_ids=[e["id"] for e in items],
                        output=items, duration_ms=_ms(t0))
        return {**state, "evidence": items}
    if event["step"] == "check":
        result = evidence.recovery(_client("cloudwatch"), params["api_url"], float(os.environ.get("RECOVERY_P95_MS", "1000")))
        attempts = int((state.get("check") or {}).get("attempts", 0)) + 1
        if result["recovered"]:
            _audit().record(inc["id"], "check", detail=f"recovered: API p95 {result['api_p95_ms']} ms "
                            f"< {result['threshold_ms']:.0f} ms", output=result, duration_ms=_ms(t0))
        return {**state, "check": {**result, "attempts": attempts}}
    raise ValueError(event["step"])


# ====================================================================== agents
def triage_fn(event: dict, _ctx=None) -> dict:
    state, t0 = event["state"], time.time()
    alarm, inc, ev = state["alarm"], state["incident"], state["evidence"]
    actions = _actions(alarm)
    doc = agents.load("triage")
    payload = {"alarm": alarm["name"], "evidence": [{k: e[k] for k in ("id", "summary")} for e in ev],
               "safe_actions": [{"id": a["id"], "applies_to": a.get("applies_to")} for a in actions]}
    try:
        res = agents.run(_client("bedrock-runtime"), doc, payload)
        hyps = analysis.sanitize_hypotheses(res.output["hypotheses"], ev, actions)
        if not hyps:
            raise agents.AgentUnavailable("no usable hypotheses (all uncited or out of scope)")
        triage = {"mode": "model", "model": doc.meta["model"], "input_tokens": res.input_tokens,
                  "output_tokens": res.output_tokens, "cost_usd": res.cost_usd}
    except agents.AgentUnavailable as exc:
        hyps = analysis.deterministic_triage(ev, actions)
        triage = {"mode": "deterministic", "model": None, "reason": str(exc)[:200],
                  "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    triage["hypotheses"] = analysis.number(hyps)
    _audit().record(inc["id"], "triage", agent="triage", instruction_sha256=doc.sha256, model=triage["model"],
                    input_tokens=triage["input_tokens"], output_tokens=triage["output_tokens"],
                    cost_usd=triage["cost_usd"], evidence_ids=sorted({c for h in hyps for c in h["cites"]}),
                    detail=f"{triage['mode']}: {len(hyps)} hypotheses", output=triage, duration_ms=_ms(t0))
    return {**state, "triage": triage}


def verify_fn(event: dict, _ctx=None) -> dict:
    state, t0 = event["state"], time.time()
    alarm, inc, ev, triage = state["alarm"], state["incident"], state["evidence"], state["triage"]
    hyps = triage["hypotheses"]
    result = analysis.verify(hyps, ev)
    doc = agents.load("verifier")
    usage = {"model": None, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "mode": "deterministic"}
    try:
        payload = {"evidence": [{k: e[k] for k in ("id", "summary")} for e in ev], "hypotheses": hyps,
                   "checks": [{"hypothesis": v["hypothesis"], "verdict": v["verdict"], "checks": v["checks"]}
                              for v in result["verdicts"]]}
        res = agents.run(_client("bedrock-runtime"), doc, payload)
        result = analysis.merge_model_verdicts(result, res.output["verdicts"])
        usage = {"model": doc.meta["model"], "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                 "cost_usd": res.cost_usd, "mode": "model"}
    except agents.AgentUnavailable as exc:
        usage["reason"] = str(exc)[:200]
    proposal = analysis.choose_action(hyps, result, _actions(alarm))
    if proposal and not runbooks.executable(proposal["spec"]):
        proposal = None  # the core only executes runtime AppConfig actions
    summary = ", ".join(f"{v['hypothesis']} {v['verdict']}" for v in result["verdicts"])
    _audit().record(inc["id"], "verify", agent="verifier", instruction_sha256=doc.sha256, model=usage["model"],
                    input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"], cost_usd=usage["cost_usd"],
                    evidence_ids=sorted({c for v in result["verdicts"] for ch in v["checks"] for c in ch["cites"]}),
                    detail=f"{usage['mode']}: {summary}; proposal: {proposal['action_id'] if proposal else 'none'}",
                    output=result, duration_ms=_ms(t0))
    return {**state, "verification": result, "proposal": proposal}


# ========================================================================= act
def act_fn(event: dict, _ctx=None) -> dict:
    """Execute the approved action. Refuses anything that isn't exactly the proposal."""
    state, t0 = event["state"], time.time()
    alarm, inc, proposal, approval = state["alarm"], state["incident"], state["proposal"], state["approval"]
    if approval.get("decision") != "approve" or approval.get("action") != proposal["id"]:
        raise ValueError("approval doesn't match the proposal")
    spec = next(a for a in _actions(alarm) if a["id"] == proposal["action_id"])  # re-read from the runbook
    if not runbooks.executable(spec):
        raise ValueError(f"{spec['id']} is not executable by the loop")
    params, shared = _customer(alarm)
    result = appconfig_change.set_keys(
        _client("appconfig"), appconfig_change.Target.from_params(params, shared),
        spec["appconfig"]["profile"], spec["appconfig"]["set"],
        f"incident {inc['id']} {proposal['id']} {spec['id']} approved by {approval.get('approver')}")
    _audit().record(inc["id"], "act", detail=f"{spec['id']}: AppConfig deployment #{result['deployment']} "
                    f"live in {result['seconds']} s", approver=approval.get("approver"),
                    approval_comment=approval.get("comment_url"), output=result, duration_ms=_ms(t0))
    return {**state, "act": result}
