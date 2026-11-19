"""The readiness policy's runbook list must match ops/runbooks, or a new alarm's
runbook could be rejected (or a deleted runbook still accepted)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readiness_runbooks_match_the_files():
    rego = (ROOT / "platform/policy/readiness.rego").read_text()
    m = re.search(r"^runbooks := \{([^}]*)\}", rego, re.M)
    in_policy = set(re.findall(r'"([a-z0-9-]+)"', m.group(1)))
    on_disk = {p.stem for p in (ROOT / "ops/runbooks").glob("*.md") if p.name != "README.md"}
    assert in_policy == on_disk


def test_every_customer_alarm_tag_names_a_runbook_file():
    tf = "".join((ROOT / "platform/modules/streaming-service" / f).read_text() for f in ("alarms.tf", "canary.tf"))
    tags = set(re.findall(r'runbook = "([a-z0-9-]+)"', tf))
    assert tags and all((ROOT / "ops/runbooks" / f"{t}.md").exists() for t in tags)
