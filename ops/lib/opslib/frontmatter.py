"""Markdown files with YAML front matter (runbooks, agent instruction files)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Document:
    meta: dict[str, Any]
    body: str
    sha256: str  # of the whole file: recorded in the audit trail
    path: str


def parse(text: str, path: str = "<string>") -> Document:
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: no front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{path}: front matter not closed")
    meta = yaml.safe_load(text[4:end]) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: front matter is not a mapping")
    return Document(meta, text[end + 5 :].lstrip("\n"), hashlib.sha256(text.encode()).hexdigest(), path)


def load(path: Path) -> Document:
    return parse(path.read_text(), str(path))
