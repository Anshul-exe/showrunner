"""The S2 drill's bad build: the fault is only in the temp zip, and only on /search."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "deploy"))
import s2_drill  # noqa: E402


def _zip(path: Path, handler: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("catalog_api/handler.py", (1980, 1, 1, 0, 0, 0)), handler)
        z.writestr(zipfile.ZipInfo("srkit/__init__.py", (1980, 1, 1, 0, 0, 0)), "")
    return path


def test_bad_build_adds_the_fault_to_search_only(tmp_path):
    real = (ROOT / "services/catalog-api/catalog_api/handler.py").read_text()
    bad = s2_drill.make_bad(_zip(tmp_path / "good.zip", real), tmp_path / "bad.zip", 0.2)
    with zipfile.ZipFile(bad) as z:
        text = z.read("catalog_api/handler.py").decode()
        assert z.namelist() == ["catalog_api/handler.py", "srkit/__init__.py"]
        assert z.getinfo("catalog_api/handler.py").date_time == (1980, 1, 1, 0, 0, 0)
    head, tail = text.split(s2_drill.SEARCH_BRANCH)
    assert "S2 drill" not in head and tail.splitlines()[0].strip().startswith('if __import__("random")')
    compile(text, "handler.py", "exec")  # still valid Python
    assert "S2 drill" not in real  # nothing faulty in the repo


def test_per_minute_counts_versions_and_errors():
    rows = [(0, 200, "6"), (1, 200, "7"), (2, 500, "7"), (61, 200, "6"), (62, -1, "?")]
    assert s2_drill.per_minute(rows) == [
        {"minute": "00:00", "6": {"n": 1, "errors": 0}, "7": {"n": 2, "errors": 1}},
        {"minute": "00:01", "6": {"n": 1, "errors": 0}, "?": {"n": 1, "errors": 1}},
    ]
