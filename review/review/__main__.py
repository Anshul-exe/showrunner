"""CLI for review-agents.yml (and local runs).

    python -m review route    < changed-paths.txt                  -> {"iac-security": [...]}
    python -m review evidence --diff d.txt --checkov c.json --files f.txt > evidence.json
    python -m review run      --evidence evidence.json --root . [--labels a,b] [--routed r.json] > result.json
    python -m review comment  < result.json                         -> Markdown
    python -m review gate     --fork false --evidence-result success ... --out DIR  -> DIR/decision.json, DIR/comment.md
    python -m review gate-record --dir DIR                       -> writes the accept-risk audit row
    exit code of `run`: 1 if a confirmed high finding and no `accept-risk` label
    `gate` always exits 0 (so the comment still gets posted); the workflow fails on decision.ok
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agents, core, gate, render


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="review")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("route")
    e = sub.add_parser("evidence")
    e.add_argument("--diff", required=True)
    e.add_argument("--checkov")
    e.add_argument("--files", required=True)
    r = sub.add_parser("run")
    r.add_argument("--evidence", required=True)
    r.add_argument("--root", default=".")
    r.add_argument("--labels", default="")
    r.add_argument("--routed", help="output of `route` (to list specialists not built yet)")
    sub.add_parser("comment")
    g = sub.add_parser("gate")
    for name in ("fork", "evidence-result", "review-needed", "review-result", "blocking", "repo", "pr", "run-url", "out"):
        g.add_argument(f"--{name}", required=name not in ("review-result", "blocking"), default="")
    for name in ("labels", "events", "comments", "highs", "review-comment"):
        g.add_argument(f"--{name}", help="JSON (or Markdown for review-comment) file; missing = empty")
    gr = sub.add_parser("gate-record")
    gr.add_argument("--dir", required=True)
    a = p.parse_args(argv)

    if a.cmd == "route":
        print(json.dumps(core.route([l.strip() for l in sys.stdin if l.strip()])))
        return 0
    if a.cmd == "evidence":
        checkov = None
        if a.checkov and Path(a.checkov).exists() and Path(a.checkov).stat().st_size:
            checkov = json.loads(Path(a.checkov).read_text())
        files = [l.strip() for l in Path(a.files).read_text().splitlines() if l.strip()]
        print(json.dumps(core.build_evidence(Path(a.diff).read_text(), checkov, files), indent=1))
        return 0
    if a.cmd == "run":
        evidence = json.loads(Path(a.evidence).read_text())
        budget = agents.Budget()
        spec = core.specialist("iac-security", evidence, budget)
        ver = core.verify(spec["findings"], evidence, Path(a.root), budget)
        labels = {x for x in a.labels.split(",") if x}
        findings = {f["id"]: f for f in spec["findings"]}
        high = [v for v in ver["verdicts"] if v["verdict"] == "confirmed" and findings[v["finding"]]["severity"] == "high"]
        confirmed_high = [findings[v["finding"]] for v in high]
        routed = json.loads(Path(a.routed).read_text()) if a.routed else {"iac-security": []}
        result = {"routed": routed, "specialist": spec, "confirmed_high": confirmed_high,
                  "verification": ver, "model_calls": budget.used, "accepted": "accept-risk" in labels,
                  "blocking": bool(high) and "accept-risk" not in labels}
        print(json.dumps(result, indent=1))
        return 1 if result["blocking"] else 0
    if a.cmd == "comment":
        res = json.load(sys.stdin)
        built = {n: s["built"] for n, s in core.SPECIALISTS.items()}
        print(render.comment(res["routed"], res["specialist"], res["verification"], built, res["accepted"]), end="")
        return 0
    if a.cmd == "gate":
        return _gate(a)
    if a.cmd == "gate-record":
        out = Path(a.dir)
        d = json.loads((out / "decision.json").read_text())
        if d.get("record"):
            note = gate.record(d["record"])
            body = (out / "comment.md").read_text().replace("audit row pending", note)
            (out / "comment.md").write_text(body)
            print(note)
        return 0
    return 2


def _read(path: str | None, default):
    if not path or not Path(path).exists() or not Path(path).stat().st_size:
        return default
    text = Path(path).read_text()
    return text if isinstance(default, str) else json.loads(text)


def _gate(a) -> int:
    yes = lambda v: str(v).lower() == "true"  # noqa: E731
    labels = _read(a.labels, [])
    d = gate.decide(
        fork=yes(a.fork), evidence_result=a.evidence_result, review_needed=yes(a.review_needed),
        review_result=a.review_result, blocking=yes(a.blocking),
        labels=[l["name"] if isinstance(l, dict) else l for l in labels],
        events=_read(a.events, []), comments=_read(a.comments, []), highs=_read(a.highs, []),
        repo=a.repo, pr=int(a.pr), run_url=a.run_url)
    review_comment = _read(a.review_comment, "")
    if review_comment and yes(a.review_needed) and a.review_result == "success":
        body = review_comment + gate.comment_suffix(d)
    else:
        body = (f"{render.MARKER}\n## Review agents\n\n**Not reviewed: {d.message}.**\n\n"
                f"**Check result: {'passed' if d.ok else 'failed'}.** [Run]({a.run_url})\n")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "decision.json").write_text(json.dumps({"ok": d.ok, "message": d.message, "record": d.record,
                                                   "comment": d.comment}, indent=1))
    (out / "comment.md").write_text(body)
    print(("PASS: " if d.ok else "FAIL: ") + d.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
