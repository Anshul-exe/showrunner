"""/approve and /reject for the incident loop (approve.yml). Stdlib only.

    approve.py check   is the commenter allowed? which command? -> $GITHUB_OUTPUT
    approve.py send    find the task token the App posted, write the decision for
                       `aws stepfunctions send-task-success`

Who may approve: a CODEOWNER (.github/CODEOWNERS) whose repo role is admin or maintain.
Where the token comes from: only a comment made by the incident loop's GitHub App
(performed_via_github_app.id == SR_APP_ID). A token pasted by anyone else is ignored.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = re.compile(r"^\s*/(approve|reject)\s+(A[0-9]+)\b[ \t]*(.*)$", re.IGNORECASE)
MARK = re.compile(r"<!-- sr-task-token (A[0-9]+) ([A-Za-z0-9+/=_\-.:]+) -->")
APPROVER_ROLES = {"admin", "maintain"}


def parse_command(body: str) -> tuple[str, str, str] | None:
    first = (body or "").strip().splitlines()[0] if (body or "").strip() else ""
    m = COMMAND.match(first)
    if not m:
        return None
    reason = m.group(3).strip().replace("@", "(at)").replace("<!--", "").replace("-->", "")[:200]
    return m.group(1).lower(), m.group(2).upper(), reason


def codeowners(text: str) -> set[str]:
    owners = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        owners |= {o[1:].lower() for o in line.split()[1:] if o.startswith("@") and "/" not in o}
    return owners


def find_token(comments: list[dict], action: str, app_id: int) -> str | None:
    for c in reversed(comments):  # newest proposal wins
        app = c.get("performed_via_github_app") or {}
        if app.get("id") != app_id:
            continue
        for a, token in MARK.findall(c.get("body") or ""):
            if a == action:
                return token
    return None


def _gh(path: str) -> object:
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}")
    req.add_header("Authorization", f"Bearer {os.environ['GH_TOKEN']}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _comments(issue: str) -> list[dict]:
    out, page = [], 1
    while True:
        batch = _gh(f"/issues/{issue}/comments?per_page=100&page={page}")
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def _output(**kv: str) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def check() -> int:
    actor = os.environ["ACTOR"]
    cmd = parse_command(os.environ.get("BODY", ""))
    if not cmd:
        print("not an /approve or /reject command")
        _output(ok="false")
        return 0
    role = _gh(f"/collaborators/{actor}/permission")["role_name"]
    owners = codeowners((ROOT / ".github" / "CODEOWNERS").read_text())
    allowed = role in APPROVER_ROLES and actor.lower() in owners
    print(f"{actor}: role {role}, codeowner {actor.lower() in owners} -> {'allowed' if allowed else 'NOT allowed'}")
    decision, action, _ = cmd
    _output(ok=str(allowed).lower(), decision=decision, action=action)
    return 0


def send() -> int:
    decision, action, reason = parse_command(os.environ["BODY"])
    token = find_token(_comments(os.environ["ISSUE"]), action, int(os.environ["SR_APP_ID"]))
    if not token:
        print(f"no task token for {action} from the incident loop's App on this issue")
        return 1
    tmp = Path(os.environ["RUNNER_TEMP"])
    (tmp / "task-token").write_text(token)  # passed to the AWS CLI by file, never echoed
    (tmp / "task-output.json").write_text(json.dumps({
        "decision": decision, "action": action, "approver": os.environ["ACTOR"],
        "comment_url": os.environ["COMMENT_URL"], "reason": reason or "(no reason given)",
    }))
    print(f"{decision} {action} by {os.environ['ACTOR']}: token found")
    return 0


if __name__ == "__main__":
    sys.exit({"check": check, "send": send}[sys.argv[1]]())
