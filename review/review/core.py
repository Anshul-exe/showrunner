"""Router, evidence, the specialist (or its rule-based fallback), and the verifier.

    route(paths)                    which specialists a PR needs (rules, no AI)
    build_evidence(diff, checkov)   E1..En: changed hunks of reviewed files + checkov hits
    specialist(...)                 findings, each citing evidence IDs and a file:line
    verify(...)                     try to DISPROVE each finding with code; the model
                                    only decides what the checks leave open
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Callable

from . import agents, diff, iam

# ------------------------------------------------------------------ router
# The full design has five specialists (BRIEF Pillar 4); tier 2 builds iac-security.
SPECIALISTS = {
    "iac-security": {"built": True, "paths": ["platform/**/*.tf", "platform/*.tf"]},
    "supply-chain": {"built": False, "paths": [".github/workflows/*", "**/package*.json", "uv.lock", "**/requirements*.txt"]},
    "data-exposure": {"built": False, "paths": ["services/**", "ops/**/*.py"]},
    "reliability": {"built": False, "paths": ["platform/**/alarms*.tf", "ops/runbooks/*"]},
    "cost": {"built": False, "paths": ["platform/**/*.tf"]},
}


def _match(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", ""))


def route(paths: list[str]) -> dict[str, list[str]]:
    """{specialist: [files it reviews]} for every specialist a changed path selects."""
    out: dict[str, list[str]] = {}
    for name, spec in SPECIALISTS.items():
        hits = [p for p in paths if any(_match(p, pat) for pat in spec["paths"])]
        if hits:
            out[name] = hits
    return out


# ------------------------------------------------------------------ evidence
def build_evidence(diff_text: str, checkov: dict | list | None, files: list[str]) -> list[dict]:
    parsed = diff.parse(diff_text)
    ev: list[dict] = []
    for path in files:
        fd = parsed.get(path)
        if not fd:
            continue
        for h in fd.hunks:
            if not h.added:
                continue
            ev.append({"id": f"E{len(ev) + 1}", "kind": "hunk", "file": path,
                       "lines": [min(h.added), max(h.added)], "added": h.added,
                       "summary": f"{path} lines {min(h.added)}-{max(h.added)} (added/changed):\n{h.text()}"})
    for c in _checkov_failures(checkov):
        f = c["file"]
        fd = parsed.get(f)
        lo, hi = c["lines"]
        if not fd or not any(lo <= n <= hi for n in fd.added):
            continue  # not touched by this PR
        ev.append({"id": f"E{len(ev) + 1}", "kind": "checkov", "file": f, "lines": [lo, hi],
                   "check": c["check_id"],
                   "summary": f"checkov {c['check_id']} FAILED at {f}:{lo}-{hi} ({c['resource']}): {c['name']}"})
    return ev


def _checkov_failures(report: dict | list | None) -> list[dict]:
    if not report:
        return []
    reports = report if isinstance(report, list) else [report]
    out = []
    for r in reports:
        for c in r.get("results", {}).get("failed_checks", []):
            path = c.get("repo_file_path") or c.get("file_path", "")
            out.append({"file": path.lstrip("/"), "lines": list(c.get("file_line_range") or [0, 0]),
                        "check_id": c.get("check_id"), "name": c.get("check_name"), "resource": c.get("resource")})
    return out


# ------------------------------------------------------------------ specialist
def specialist(name: str, evidence: list[dict], budget: agents.Budget,
               call: Callable[[dict], dict] | None = None) -> dict[str, Any]:
    agent = agents.load(name)
    payload = {"evidence": [{"id": e["id"], "summary": e["summary"]} for e in evidence]}
    try:
        res = agents.run(agent, payload, budget, call)
        findings = sanitize(res.output["findings"], evidence)
        return {"mode": "model", "agent": name, "sha256": agent.sha256, "model": agent.meta["model"],
                "tokens": [res.input_tokens, res.output_tokens], "cost_usd": res.cost_usd, "findings": findings}
    except agents.AgentUnavailable as exc:
        return {"mode": "rules", "agent": name, "sha256": agent.sha256, "model": None, "reason": str(exc)[:200],
                "tokens": [0, 0], "cost_usd": 0.0, "findings": rule_findings(evidence)}


def sanitize(raw: list[dict], evidence: list[dict]) -> list[dict]:
    ids = {e["id"] for e in evidence}
    out = []
    for f in raw[:5]:
        cites = [c for c in f.get("cites", []) if c in ids]
        if not cites:
            continue  # an uncited finding is dropped, not shown
        out.append({"id": f"F{len(out) + 1}", "category": f["category"], "severity": f["severity"],
                    "file": str(f["file"]).lstrip("./"), "line": int(f["line"]), "title": str(f["title"])[:120],
                    "claim": str(f["claim"])[:400], "cites": cites})
    return out


def rule_findings(evidence: list[dict]) -> list[dict]:
    """No model: checkov's failed checks on changed lines are the findings."""
    out = []
    for e in evidence:
        if e["kind"] != "checkov":
            continue
        cat = "iam-wildcard" if any(k in (e.get("check") or "") for k in ("CKV_AWS_1", "CKV_AWS_49", "CKV_AWS_355",
                                                                           "CKV_AWS_290", "CKV_AWS_107")) else "other"
        out.append({"id": f"F{len(out) + 1}", "category": cat, "severity": "medium", "file": e["file"],
                    "line": e["lines"][0], "title": e["summary"].split(": ", 1)[-1][:120],
                    "claim": e["summary"], "cites": [e["id"]]})
    return out


# ------------------------------------------------------------------ verifier
def _check(name: str, result: str, detail: str) -> dict:
    return {"name": name, "result": result, "detail": detail}


def checks_for(f: dict, evidence: list[dict], root: Path) -> list[dict]:
    checks = []
    hunks = [e for e in evidence if e["kind"] == "hunk" and e["file"] == f["file"]]
    near = any(any(abs(n - f["line"]) <= 3 for n in e["added"]) for e in hunks)
    if not near:
        checks.append(_check("in-this-pr", "fail", f"{f['file']}:{f['line']} isn't a line this PR adds or changes"))
        return checks
    checks.append(_check("in-this-pr", "pass", f"{f['file']}:{f['line']} is changed by this PR"))
    path = root / f["file"]
    if f["category"] == "iam-wildcard":
        st = iam.statement_at(path.read_text(), f["line"]) if path.exists() else None
        if st is None:
            checks.append(_check("statement", "unknown", "no IAM policy statement found around that line"))
        elif st.effect.lower() == "deny":
            checks.append(_check("grants-access", "fail",
                                 f"lines {st.start}-{st.end} are a Deny statement: a wildcard there narrows access, "
                                 "it grants nothing"))
        elif not (st.wildcard_action or st.wildcard_resource):
            checks.append(_check("wildcard", "fail", f"the statement at lines {st.start}-{st.end} has no wildcard "
                                 f"(actions {st.actions}, resources {st.resources})"))
        elif not st.wildcard_action and st.actions and all(a in iam.NO_RESOURCE_TYPE for a in st.actions):
            checks.append(_check("resource-type", "fail",
                                 f"{', '.join(st.actions)} {'has' if len(st.actions) == 1 else 'have'} no IAM resource type: "
                                 "`*` is the only valid resource (see docs/adr/0008-approver-role-scope.md)"))
        else:
            what = [x for x in (("actions " + str(st.actions)) if st.wildcard_action else None,
                                ("resources " + str(st.resources)) if st.wildcard_resource else None) if x]
            checks.append(_check("grants-access", "pass",
                                 f"Allow statement at lines {st.start}-{st.end} with wildcard {' and '.join(what)}"))
    hits = [e for e in evidence if e["kind"] == "checkov" and e["file"] == f["file"]
            and e["lines"][0] - 3 <= f["line"] <= e["lines"][1] + 3]
    if hits:
        checks.append(_check("scanner-agrees", "pass", "; ".join(e["summary"] for e in hits)[:300]))
    return checks


def verdict_from(checks: list[dict]) -> str:
    results = [c["result"] for c in checks]
    if "fail" in results:
        return "refuted"
    if any(c["name"] in ("grants-access", "scanner-agrees") and c["result"] == "pass" for c in checks):
        return "confirmed"
    return "inconclusive"


def verify(findings: list[dict], evidence: list[dict], root: Path, budget: agents.Budget,
           call: Callable[[dict], dict] | None = None) -> dict[str, Any]:
    verdicts = []
    for f in findings:
        checks = checks_for(f, evidence, root)
        verdicts.append({"finding": f["id"], "verdict": verdict_from(checks), "checks": checks,
                         "decided_by": "checks", "explanation": ""})
    open_ = [v for v in verdicts if v["verdict"] == "inconclusive"]
    usage: dict[str, Any] = {"mode": "checks only", "tokens": [0, 0], "cost_usd": 0.0}
    if open_:  # the model gets only what the checks couldn't settle
        agent = agents.load("review-verifier")
        usage["sha256"] = agent.sha256
        by_id = {f["id"]: f for f in findings}
        payload = {"findings": [by_id[v["finding"]] for v in open_],
                   "checks": [{"finding": v["finding"], "checks": v["checks"]} for v in open_],
                   "code": {v["finding"]: _context(root, by_id[v["finding"]]) for v in open_}}
        try:
            res = agents.run(agent, payload, budget, call)
            model = {m["finding"]: m for m in res.output["verdicts"]}
            for v in open_:
                m = model.get(v["finding"])
                if m:
                    v.update(verdict=m["verdict"], decided_by="model", explanation=str(m["explanation"])[:300])
            usage = {"mode": "model", "model": agent.meta["model"], "sha256": agent.sha256,
                     "tokens": [res.input_tokens, res.output_tokens], "cost_usd": res.cost_usd}
        except agents.AgentUnavailable as exc:
            usage["reason"] = str(exc)[:200]
    return {"verdicts": verdicts, "usage": usage}


def _context(root: Path, f: dict, radius: int = 12) -> str:
    path = root / f["file"]
    if not path.exists():
        return ""
    lines = path.read_text().splitlines()
    lo, hi = max(0, f["line"] - radius - 1), min(len(lines), f["line"] + radius)
    return "\n".join(f"{i + 1:>5} {lines[i]}" for i in range(lo, hi))
