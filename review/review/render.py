"""One PR comment: confirmed findings in a table; refuted ones collapsed with the reason,
so the human sees what was filtered and why; open ones flagged for a human."""

from __future__ import annotations

from typing import Any

MARKER = "<!-- sr-review -->"
SEV = {"high": 0, "medium": 1, "low": 2}


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def comment(routed: dict[str, list[str]], spec: dict[str, Any], ver: dict[str, Any], built: dict[str, bool],
            accepted: bool = False, degraded: str = "", budget_reached: bool = False) -> str:
    findings = {f["id"]: f for f in spec["findings"]}
    verdicts = sorted(ver["verdicts"], key=lambda v: SEV.get(findings[v["finding"]]["severity"], 9))
    confirmed = [v for v in verdicts if v["verdict"] == "confirmed"]
    refuted = [v for v in verdicts if v["verdict"] == "refuted"]
    open_ = [v for v in verdicts if v["verdict"] not in ("confirmed", "refuted")]
    high = [v for v in confirmed if findings[v["finding"]]["severity"] == "high"]

    who = (f"model `{spec['model']}` ({spec['tokens'][0]}+{spec['tokens'][1]} tokens, ${spec['cost_usd']:.5f})"
           if spec["mode"] == "model" else f"rules: checkov's failures on changed lines (model not used: {spec.get('reason')})")
    vu = ver["usage"]
    vwho = (f"checks, then model `{vu['model']}` for {sum(1 for v in verdicts if v['decided_by'] == 'model')} open finding(s)"
            if vu["mode"] == "model" else "checks only" + (f" (model not used: {vu['reason']})" if vu.get("reason") else ""))
    out = [MARKER, "## Review agents", ""]
    if degraded:
        out += [f"**Not reviewed by the model: {_cell(degraded)}.** The deterministic checks below ran, but "
                "without the model this PR wasn't reviewed, so the check fails closed.", ""]
    elif budget_reached:
        out += ["**Not reviewed by the model: daily budget reached.** The result below is the scanner's "
                "rules and the verifier's checks only (a designed limit, not an error): a human should read "
                "the diff.", ""]
    out += [f"**{len(confirmed)} confirmed**, {len(refuted)} refuted by the verifier, {len(open_)} for a human. "
           f"Specialist `{spec['agent']}`: {who}; instructions sha256 `{spec['sha256'][:12]}`. Verifier: {vwho}.", ""]
    if confirmed:
        out += ["| Severity | Where | Finding | Evidence |", "|---|---|---|---|"]
        for v in confirmed:
            f = findings[v["finding"]]
            ev = "; ".join(c["detail"] for c in v["checks"] if c["result"] == "pass" and c["name"] != "in-this-pr")
            out.append(f"| **{f['severity']}** | `{f['file']}:{f['line']}` | {_cell(f['title'])}: {_cell(f['claim'])} "
                       f"({', '.join(f['cites'])}) | {_cell(ev or v['explanation'])} |")
        out.append("")
    if open_:
        out += ["**Needs a human** (the checks couldn't settle these):", ""]
        for v in open_:
            f = findings[v["finding"]]
            out.append(f"- `{f['file']}:{f['line']}` {_cell(f['title'])} ({f['severity']}): {_cell(v['explanation'] or 'no check applies')}")
        out.append("")
    if refuted:
        out += [f"<details><summary>{len(refuted)} finding(s) refuted by the verifier (not counted)</summary>", ""]
        for v in refuted:
            f = findings[v["finding"]]
            why = "; ".join(c["detail"] for c in v["checks"] if c["result"] == "fail") or v["explanation"]
            out.append(f"- ~~`{f['file']}:{f['line']}` {_cell(f['title'])} ({f['severity']})~~: {_cell(why)}")
        out += ["", "</details>", ""]
    if degraded:
        out.append("**Check result: failed** (not reviewed: an infrastructure error, not a finding). "
                   "Fix it, then re-run failed jobs; `accept-risk` doesn't override this.")
    elif high:
        out.append("**Check result: failed** (a confirmed high). " + (
            "Overridden: the `accept-risk` label is set; the reason belongs in a PR comment." if accepted else
            "To override, add the label `accept-risk` and write the reason in a comment."))
    else:
        out.append("**Check result: passed** (no confirmed high).")
    skipped = [n for n in routed if not built.get(n)]
    if skipped:
        out += ["", f"_Routed but not built yet (tier 3): {', '.join(f'`{n}`' for n in skipped)}._"]
    return "\n".join(out) + "\n"
