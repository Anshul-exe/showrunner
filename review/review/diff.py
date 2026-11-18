"""Parse `git diff --unified=3 base...head` into what the review needs: per file, the
added lines with their new line numbers, and each hunk as readable text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    start: int
    lines: list[tuple[int | None, str, str]] = field(default_factory=list)  # (new line no, op, text)

    @property
    def added(self) -> list[int]:
        return [n for n, op, _ in self.lines if op == "+" and n is not None]

    def text(self, limit: int = 60) -> str:
        out = []
        for n, op, t in self.lines[:limit]:
            out.append(f"{'' if n is None else n:>5} {op} {t}")
        if len(self.lines) > limit:
            out.append(f"      ... {len(self.lines) - limit} more lines")
        return "\n".join(out)


@dataclass
class FileDiff:
    path: str
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> set[int]:
        return {n for h in self.hunks for n in h.added}


def parse(diff: str) -> dict[str, FileDiff]:
    files: dict[str, FileDiff] = {}
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    new_no = 0
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            cur, hunk = None, None
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                cur = None
                continue
            path = path[2:] if path.startswith("b/") else path
            cur = files.setdefault(path, FileDiff(path))
            continue
        if raw.startswith("--- ") or cur is None:
            continue
        m = HUNK.match(raw)
        if m:
            new_no = int(m.group(1))
            hunk = Hunk(new_no)
            cur.hunks.append(hunk)
            continue
        if hunk is None or raw.startswith("\\"):
            continue
        op, text = (raw[0], raw[1:]) if raw else (" ", "")
        if op == "-":
            hunk.lines.append((None, "-", text))
        elif op in "+ ":
            hunk.lines.append((new_no, op, text))
            new_no += 1
    return files
