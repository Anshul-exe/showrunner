"""CLI for review-agents.yml (and local runs).

    python -m review route    < changed-paths.txt                  -> {"iac-security": [...]}
    python -m review evidence --diff d.txt --checkov c.json --files f.txt > evidence.json
    python -m review run      --evidence evidence.json --root . [--labels a,b] [--routed r.json] > result.json
    python -m review comment  < result.json                         -> Markdown
    exit code of `run`: 1 if a confirmed high finding and no `accept-risk` label
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agents, core, render


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
        routed = json.loads(Path(a.routed).read_text()) if a.routed else {"iac-security": []}
        result = {"routed": routed, "specialist": spec,
                  "verification": ver, "model_calls": budget.used, "accepted": "accept-risk" in labels,
                  "blocking": bool(high) and "accept-risk" not in labels}
        print(json.dumps(result, indent=1))
        return 1 if result["blocking"] else 0
    if a.cmd == "comment":
        res = json.load(sys.stdin)
        built = {n: s["built"] for n, s in core.SPECIALISTS.items()}
        print(render.comment(res["routed"], res["specialist"], res["verification"], built, res["accepted"]), end="")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
