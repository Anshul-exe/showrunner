"""The `review-agents gate` check: pass or fail, fail closed, and record overrides.

The gate job always runs (`if: always()`), because GitHub counts a *skipped* required
check as passing. So every way the review can fail to happen ends here as a failure:
a fork PR (no AWS token for forks, the evidence job is skipped), a scanner crash, a
failed review job. Order of the rules matters and is tested.

An `accept-risk` override needs the label AND a reason comment by whoever added the
label; it's then written to `sr-ops-audit` (pk `REVIEW#pr-<n>`, PutItem with
attribute_not_exists, so no row is overwritten; a re-run finds its row and moves on).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import render

AUDIT_TABLE = "sr-ops-audit"
LABEL = "accept-risk"
REASON_WINDOW = timedelta(hours=1)  # a reason may be written shortly before the label
RETENTION_S = 365 * 24 * 3600  # an accepted risk is kept a year (incident rows: 90 days)


@dataclass
class Decision:
    ok: bool
    message: str
    record: dict[str, Any] | None = None  # the audit item, when an override is accepted
    comment: bool = True  # post/refresh the PR comment (not possible on a fork PR)
    details: dict[str, Any] = field(default_factory=dict)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def label_event(events: list[dict]) -> dict | None:
    """The latest `labeled accept-risk` event: who and when."""
    hits = [e for e in events if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == LABEL]
    return hits[-1] if hits else None


def reason_comment(comments: list[dict], actor: str, labeled_at: str) -> dict | None:
    """The latest comment by the person who added the label, written at most an hour
    before the label or any time after; the review bot's own comment doesn't count."""
    since = _ts(labeled_at) - REASON_WINDOW
    hits = [c for c in comments if (c.get("user") or {}).get("login") == actor
            and not str(c.get("body", "")).startswith(render.MARKER)
            and str(c.get("body", "")).strip() and _ts(c["created_at"]) >= since]
    return hits[-1] if hits else None


def decide(*, fork: bool, evidence_result: str, review_needed: bool, review_result: str,
           blocking: bool, labels: list[str], events: list[dict], comments: list[dict],
           highs: list[dict], repo: str, pr: int, run_url: str, degraded: str = "") -> Decision:
    if fork:
        return Decision(False, "not reviewed automatically: fork PR (fork PRs get no AWS token); "
                               "a maintainer reviews it by hand", comment=False)
    if evidence_result != "success":
        # A cancelled run was superseded by a newer push: still fail (for its commit), but
        # don't overwrite the newer run's comment.
        return Decision(False, f"not reviewed: the evidence job's result is '{evidence_result or 'none'}' "
                               "(a scanner crash or a cancelled run); failing closed",
                        comment=evidence_result != "cancelled")
    if not review_needed:
        return Decision(True, "no Terraform changes: nothing for iac-security", comment=False)
    if degraded:  # the model couldn't review it (not the daily budget): no label overrides this
        return Decision(False, f"not reviewed by the model ({degraded}); an infrastructure error, not a finding: "
                               "failing closed. Fix it, then re-run failed jobs")
    if review_result != "success":
        return Decision(False, f"not reviewed: the review job's result is '{review_result or 'none'}'; failing closed")
    if not blocking:
        return Decision(True, "no confirmed high finding")
    if LABEL not in labels:
        return Decision(False, f"a confirmed high finding; to override, add the label `{LABEL}`, write the reason "
                               "in a comment, then re-run failed jobs")
    ev = label_event(events)
    if ev is None:
        return Decision(False, f"`{LABEL}` is set but its label event wasn't found; failing closed")
    actor, at = ev["actor"]["login"], ev["created_at"]
    reason = reason_comment(comments, actor, at)
    if reason is None:
        return Decision(False, f"`{LABEL}` was added by {actor} at {at}, but there's no reason: {actor} must write "
                               "it in a PR comment, then re-run failed jobs")
    item = {
        "pk": f"REVIEW#pr-{pr}", "sk": f"{at}#accept-risk", "step": "accept-risk",
        "by": actor, "at": at, "pr": f"https://github.com/{repo}/pull/{pr}",
        "findings": [{k: h[k] for k in ("id", "severity", "file", "line", "title") if k in h} for h in highs],
        "reason_comment": reason["html_url"], "run": run_url,
        "expires_at": int(_ts(at).timestamp()) + RETENTION_S,
    }
    return Decision(True, f"overridden: `{LABEL}` added by {actor} at {at}; reason: {reason['html_url']}",
                    record=item, details={"by": actor, "at": at, "reason": reason["html_url"]})


# ------------------------------------------------------------------ DynamoDB
def to_ddb(value: Any) -> dict:
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, dict):
        return {"M": {k: to_ddb(v) for k, v in value.items()}}
    if isinstance(value, list):
        return {"L": [to_ddb(v) for v in value]}
    return {"S": str(value)}


def put_request(item: dict) -> dict:
    """PutItem input: never overwrites (IAM can't enforce this; the code does)."""
    return {"TableName": AUDIT_TABLE, "Item": {k: to_ddb(v) for k, v in item.items()},
            "ConditionExpression": "attribute_not_exists(pk)"}


def record(item: dict, aws=None) -> str:
    from . import agents
    try:
        (aws or agents.aws)(["dynamodb", "put-item"], put_request(item))
        return f"recorded in {AUDIT_TABLE} ({item['pk']} / {item['sk']})"
    except agents.AgentUnavailable as exc:
        if "ConditionalCheckFailed" in str(exc):
            return f"already recorded in {AUDIT_TABLE} ({item['pk']} / {item['sk']})"
        raise


def comment_suffix(d: Decision, recorded: str = "") -> str:
    if d.record:
        return (f"\n**Override:** `{LABEL}` added by {d.details['by']} at {d.details['at']}; "
                f"[reason]({d.details['reason']}); {recorded or 'audit row pending'}. The gate passes.\n")
    return ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
