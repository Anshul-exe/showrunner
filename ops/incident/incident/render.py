"""Markdown for the incident issue and its comments (the single incident record)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

TOKEN_MARK = "sr-task-token"
VERDICT = {"confirmed": "**CONFIRMED**", "refuted": "**REFUTED**", "inconclusive": "**inconclusive**",
           "not-checkable": "not checkable", "not-enough-history": "**not enough history**"}


def clean(text: Any) -> str:
    """Model-written text: no @-mentions (they notify real people), no HTML comments
    (they could imitate the approval-token marker), one line."""
    s = re.sub(r"<!--|-->", "", str(text))
    s = s.replace("@", "(at)")
    return " ".join(s.split())


def short_reason(reason: str) -> str:
    """'ValidationException: An error occurred (...) when calling X: Operation not allowed'
    -> 'ValidationException: Operation not allowed'."""
    reason = clean(reason)
    head, _, tail = reason.partition(": ")
    msg = tail.rsplit(": ", 1)[-1] if tail else ""
    return f"{head}: {msg}"[:160] if msg else head[:160]


def title(alarm: dict) -> str:
    return f"[{alarm['severity'].upper()}] {alarm['customer']} {alarm['env']}: {alarm['kind']}"


def issue_body(alarm: dict, incident_id: str) -> str:
    return f"""**Alarm** `{alarm['name']}` entered ALARM at {alarm['time']} UTC.

> {clean(alarm['reason'])}

| | |
|---|---|
| Customer / env | `{alarm['customer']}` / `{alarm['env']}` |
| Severity | {alarm['severity']} |
| Runbook | [{alarm['kind']}]({alarm['runbook_url']}) |
| Incident ID | `{incident_id}` |

The incident loop is collecting evidence. Analysis and a proposed action follow as
comments. Nothing changes without a human's `/approve`.

_This issue is the single record of the incident: every step, approval and change is
added here and to the audit table._
"""


def joined(alarm: dict) -> str:
    return f"Alarm `{alarm['name']}` also fired at {alarm['time']} UTC: joined to this incident (one record per customer env within 30 min)."


def _evidence_table(evidence: list[dict]) -> str:
    rows = ["| ID | Evidence | Source |", "|---|---|---|"]
    for e in evidence:
        rows.append(f"| {e['id']} | {e['summary'].replace('|', '/')} | {e['source']} |")
    return "\n".join(rows)


def _checks(checks: list[dict]) -> str:
    out = []
    for c in checks:
        cites = f" ({', '.join(c['cites'])})" if c["cites"] else ""
        out.append(f"  - {c['name']}: {c['result']}: {c['detail']}{cites}")
    return "\n".join(out)


def _proposal(proposal: dict, flags: dict | None, timeout_min: int) -> list[str]:
    spec = proposal["spec"]
    ac = spec["appconfig"]
    head = [f"### Proposed action {proposal['id']}: `{spec['id']}` (for {proposal['hypothesis']})", "",
            f"{spec.get('description', '')}", "",
            f"- **Lane:** runtime: an AppConfig deployment to `{ac['profile']}`, no code deploy, no Terraform. "
            "Live in under a minute; if it makes things worse, the alarm fires again and the loop "
            "proposes undoing it (no automatic rollback: ADR 0017)."]
    if ac.get("rollback"):
        n = proposal.get("params", {}).get("deployment")
        body = [f"- **Exact change:** redeploy the `{ac['profile']}` version that was live before deployment #{n}. "
                f"Refused at execution time if anything was deployed after #{n}.",
                f"- **Now:** `{json.dumps(flags or {})}`",
                f"- **Blast radius:** {spec.get('blast_radius', 'not stated')}",
                f"- **Rollback:** {spec.get('rollback', 'not stated')}"]
    else:
        before = dict(flags or {})
        after = {**before, **ac["set"]}
        body = [f"- **Exact change** (`{ac['profile']}`):", "  ```diff",
                *[f"  - {json.dumps({k: before.get(k)})}\n  + {json.dumps({k: v})}" for k, v in ac["set"].items()],
                "  ```",
                f"- **Result:** `{json.dumps(after)}`",
                f"- **Blast radius:** {spec.get('blast_radius', 'not stated')}",
                f"- **Rollback:** `{json.dumps((spec.get('rollback') or {}).get('set', spec.get('rollback')))}` "
                f"on `{ac['profile']}`"]
    return head + body + ["",
        f"**To approve, comment `/approve {proposal['id']}`.** To reject: `/reject {proposal['id']} <reason>`. "
        f"Only a CODEOWNER with maintain or admin rights can approve. No answer in {timeout_min} min = "
        "escalation, and nothing changes."]


def analysis(evidence: list[dict], triage: dict, verification: dict, proposal: dict | None,
             flags: dict | None, task_token: str | None = None, timeout_min: int = 30,
             recommendation: dict | None = None) -> str:
    mode = triage["mode"]
    who = (f"model `{triage['model']}` ({triage['input_tokens']}+{triage['output_tokens']} tokens)"
           if mode == "model" else f"deterministic rules (model unavailable: {short_reason(triage.get('reason', ''))})")
    parts = ["## Analysis", "", "### Evidence (collected by code)", "", _evidence_table(evidence), "",
             f"### Hypotheses (triage: {who}) and the verifier's verdicts", ""]
    verdicts = {v["hypothesis"]: v for v in verification["verdicts"]}
    for h in triage["hypotheses"]:
        v = verdicts[h["id"]]
        if v["verdict"] == "not-checkable":  # one line: a lead for a human, nothing more
            parts.append(f"**{h['id']} `{h['component']}`**: not checkable automatically (no check for "
                         f"`{h['component']}`; never acted on): {clean(h['statement'])} ({', '.join(h['cites'])})")
            parts.append("")
            continue
        parts.append(f"**{h['id']} `{h['component']}`**: {VERDICT[v['verdict']]} (decided by {v['decided_by']})")
        parts.append(f"- {clean(h['statement'])} ({', '.join(h['cites'])})")
        if v.get("explanation"):
            parts.append(f"- Verifier: {clean(v['explanation'])}")
        parts.append("- Checks:\n" + _checks(v["checks"]))
        parts.append("")
    if verification.get("also_checked"):
        parts += ["### Also checked: the dependencies triage didn't name", "",
                  "A service-level dashboard can't tell these apart. Each one is checked so a wrong suspect "
                  "(e.g. the cache) is ruled out with numbers, not opinion.", ""]
        for a in verification["also_checked"]:
            parts.append(f"**`{a['component']}`**: {VERDICT[a['verdict']]}")
            parts.append(_checks(a["checks"]))
            parts.append("")
    if proposal:
        parts += _proposal(proposal, flags, timeout_min)
    else:
        parts += ["### No action proposed", "",
                  "No hypothesis was confirmed with a matching runtime action in the runbook. A human needs to "
                  "look: start with the runbook's Diagnose section."]
    if recommendation:
        spec = recommendation["spec"]
        parts += ["", f"### Recommended (for a human, through the pipeline): `{spec['id']}` "
                      f"(for {recommendation['hypothesis']})", "",
                  f"{spec.get('description', '')}", "",
                  f"- **Lane:** change: Terraform through CI, not executed by this loop.",
                  f"- **How:** `{spec.get('pipeline', 'see the runbook')}`",
                  f"- **Blast radius:** {spec.get('blast_radius', 'not stated')}",
                  f"- **Rollback:** {spec.get('rollback', 'not stated')}"]
    if task_token:
        parts += ["", f"<!-- {TOKEN_MARK} {proposal['id']} {task_token} -->"]
    return "\n".join(parts)


def note(text: str) -> str:
    return text


def _ts(row: dict) -> str:
    return row["sk"].split("#", 1)[0][11:19]


PROBLEM_THRESHOLD = 3  # real firings in 30 days


def problem_line(alarm_name: str, real: int, demo: int) -> str:
    total = real + demo
    if total == 0:
        return ""
    demo_note = f", {demo} of them during `sr demo` chaos (not counted)" if demo else ""
    if real >= PROBLEM_THRESHOLD:
        return (f"\n\n**Problem management:** `{alarm_name}` fired {total} times in 30 days{demo_note}: "
                f"{real} real, labelled `problem-management` for root-cause work.")
    if demo:
        return (f"\n\n**Problem management:** `{alarm_name}` fired {total} times in 30 days{demo_note}: "
                f"{real} real, under the threshold of {PROBLEM_THRESHOLD}: not labelled.")
    return ""


def pir(alarm: dict, incident_id: str, rows: list[dict], firings: tuple[int, int]) -> str:
    """firings = (real, demo) alarm firings in the last 30 days."""
    rows = sorted(rows, key=lambda r: r["sk"])
    alarm_t = datetime.fromisoformat(alarm["time"]).replace(tzinfo=timezone.utc)  # CloudWatch: UTC

    def after_alarm(step: str) -> str:
        r = next((r for r in rows if r["step"] == step), None)
        if not r:
            return "n/a"
        t = datetime.fromisoformat(r["sk"].split("#", 1)[0])
        return f"{(t - alarm_t).total_seconds():,.0f} s"

    def out(step: str) -> dict:
        r = next((r for r in rows if r["step"] == step), None)
        try:
            return json.loads(r["output"]) if r and r.get("output") else {}
        except ValueError:
            return {}

    timeline = ["| UTC | Step | Detail |", "|---|---|---|", f"| {alarm['time'][11:19]} | alarm | `{alarm['name']}` |"]
    for r in rows:
        detail = r.get("detail", "")
        if r.get("agent"):
            detail = f"{r['agent']} ({r.get('model', 'deterministic')}, file sha256 {str(r.get('instruction_sha256', ''))[:12]}) {detail}"
        if r.get("approver"):
            detail += f" approver: {r['approver']}"
        timeline.append(f"| {_ts(r)} | {r['step']} | {clean(detail)} |")

    verify_out, act_out, triage_out = out("verify"), out("act"), out("triage")
    confirmed = [v for v in verify_out.get("verdicts", []) if v.get("verdict") == "confirmed"]
    hyps = {h["id"]: h for h in triage_out.get("hypotheses", [])}
    cause = ("; ".join(f"{v['hypothesis']} `{hyps.get(v['hypothesis'], {}).get('component', '?')}`: "
                       f"{clean(hyps.get(v['hypothesis'], {}).get('statement', ''))}" for v in confirmed)
             or "unknown (no hypothesis confirmed)")
    changed = (f"`{act_out.get('profile')}`: `{json.dumps(act_out.get('before'))}` -> `{json.dumps(act_out.get('after'))}` "
               f"(AppConfig deployment #{act_out.get('deployment')}, live in {act_out.get('seconds')} s)"
               if act_out else "nothing")
    problem = problem_line(alarm["name"], *firings)
    return f"""## Post-incident review (draft, from the audit trail)

**Incident** `{incident_id}` · alarm -> issue {after_alarm('open')} · alarm -> proposal {after_alarm('propose')} · alarm -> mitigation {after_alarm('act')} · alarm -> recovered {after_alarm('check')}

### Timeline
{chr(10).join(timeline)}

### Root cause
{cause}

### What changed
{changed}

### Follow-ups
- [ ] Undo the mitigation when the dependency is healthy (the runbook's rollback), and record when.
- [ ] Ask the dependency's owner for their incident report; link it here.
- [ ] Check the alarm fired at the right time (threshold, evaluation periods).
- [ ] Anything the runbook should say that it didn't?{problem}
"""
