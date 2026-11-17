"""Build a Lambda zip for one service, reproducibly.

    uv run python tools/build/package.py catalog-api      -> build/catalog-api.zip

Contents: the service's package, the shared `srkit` package, and its locked runtime
dependencies installed with --require-hashes for arm64 Linux (no install scripts: wheels
only). Entries are sorted and timestamped 1980-01-01, so the same source gives the same
zip and the same hash, which keeps Terraform plans and deploys honest.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = {
    "catalog-api": "catalog_api",
    "vendor-mock": "vendor_mock",
}
EPOCH = (1980, 1, 1, 0, 0, 0)


def build(service: str, out_dir: Path = ROOT / "build") -> Path:
    if service not in SERVICES:
        raise SystemExit(f"unknown service {service!r}; one of {sorted(SERVICES)}")
    src = ROOT / "services" / service
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        reqs = src / "requirements.txt"
        if reqs.exists():
            subprocess.run(
                [
                    "uv", "pip", "install", "--quiet", "--target", str(stage),
                    "--require-hashes", "-r", str(reqs),
                    "--python-version", "3.12", "--python-platform", "aarch64-manylinux2014",
                    "--only-binary", ":all:", "--no-deps",
                ],
                check=True,
            )
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(src / SERVICES[service], stage / SERVICES[service], ignore=ignore)
        shutil.copytree(ROOT / "services" / "shared" / "srkit", stage / "srkit", ignore=ignore)

        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"{service}.zip"
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(stage).as_posix(), EPOCH)
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    return out


def source_hash(path: Path) -> str:
    """base64(sha256), the format of Lambda's CodeSha256."""
    return base64.b64encode(hashlib.sha256(path.read_bytes()).digest()).decode()


def main(argv: list[str]) -> int:
    for service in argv or sorted(SERVICES):
        out = build(service)
        print(f"{out.relative_to(ROOT)}  {source_hash(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
