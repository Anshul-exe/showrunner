"""Runbooks as code: ops/runbooks/<alarm>.md, front matter = machine-readable safe actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import frontmatter

ENVS = ("dev", "prod")


@dataclass(frozen=True)
class AlarmRef:
    name: str
    customer: str
    env: str
    kind: str  # runbook name, e.g. vendor-latency


def load_all(directory: Path) -> dict[str, frontmatter.Document]:
    out = {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        doc = frontmatter.load(path)
        out[doc.meta["alarm"]] = doc
    return out


def parse_alarm_name(name: str, kinds: list[str] | set[str]) -> AlarmRef | None:
    """sr-<customer>-<env>-<kind> -> AlarmRef. Customer names may contain hyphens, so
    match the known kinds and envs from the right."""
    for kind in sorted(kinds, key=len, reverse=True):
        m = re.fullmatch(rf"sr-(?P<customer>[a-z0-9-]+)-(?P<env>{'|'.join(ENVS)})-{re.escape(kind)}", name)
        if m:
            return AlarmRef(name, m["customer"], m["env"], kind)
    return None


def safe_actions(doc: frontmatter.Document) -> list[dict[str, Any]]:
    return list(doc.meta.get("safe_actions") or [])


def executable(action: dict[str, Any]) -> bool:
    """The core executes runtime actions that set AppConfig keys. Anything else is
    proposed for a human to carry out."""
    ac = action.get("appconfig") or {}
    return action.get("lane") == "runtime" and bool(ac.get("profile")) and isinstance(ac.get("set"), dict)
