"""The customer page's CloudFront Function, run in node (skipped where node is missing).
CloudFront's runtime is not node, so the function sticks to plain ES5-style JS."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FN = Path(__file__).resolve().parents[1] / "platform/modules/streaming-service/site/strip-api.js"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("uri,want", [
    ("/api/catalog", "/catalog"),
    ("/api/titles/t01/recs", "/titles/t01/recs"),
    ("/api", "/"),
    ("/api/", "/"),
    ("/apiary/x", "/apiary/x"),     # only the /api segment, not a prefix of a word
    ("/catalog", "/catalog"),
])
def test_strip_api(uri, want):
    js = FN.read_text() + f"\nconsole.log(JSON.stringify(handler({{request: {{uri: {json.dumps(uri)}}}}})));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert json.loads(out)["uri"] == want


def test_site_files_load_nothing_from_elsewhere():
    site = FN.parent
    html = (site / "index.html").read_text()
    assert "http://" not in html and "https://" not in html  # CSP: 'self' only
    assert "innerHTML" not in (site / "app.js").read_text()   # API data rendered as text only
