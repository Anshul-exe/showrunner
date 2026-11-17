"""Map the files a PR changes to the Terraform stacks that need a plan.

One state per stack (BRIEF section 5), so a one-line change plans one stack, not all
of them. Prints a JSON list for a GitHub Actions matrix:

    git diff --name-only origin/main...HEAD | python tools/ci/changed_stacks.py

Each entry: {"stack": "<backend key prefix>", "dir": "<terraform root>", "args": [...]}.
Stacks are discovered from the repo, so a stack only appears once its files exist.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = "platform"

# Changes here can affect every stack.
EVERYTHING = (
    f"{PLATFORM}/policy/",
    ".github/workflows/plan.yml",
    "tools/ci/changed_stacks.py",
)


def discover(root: Path = ROOT) -> dict[str, dict]:
    """All stacks that exist in the repo, keyed by their state key prefix."""
    stacks: dict[str, dict] = {}
    p = root / PLATFORM

    if (p / "bootstrap").is_dir():
        stacks["bootstrap"] = {"stack": "bootstrap", "dir": f"{PLATFORM}/bootstrap", "args": []}

    if (p / "ops-core").is_dir():
        stacks["ops-core"] = {"stack": "ops-core", "dir": f"{PLATFORM}/ops-core", "args": []}

    if (p / "shared").is_dir():
        for env in ("dev", "prod"):
            stacks[f"shared/{env}"] = {
                "stack": f"shared/{env}",
                "dir": f"{PLATFORM}/shared",
                "args": [f"-var=env={env}"],
            }

    customers = p / "customers"
    if customers.is_dir() and (p / "envs").is_dir():
        for spec in sorted(customers.glob("*/*.yaml")):
            name, env = spec.parent.name, spec.stem
            stacks[f"customers/{name}/{env}"] = {
                "stack": f"customers/{name}/{env}",
                "dir": f"{PLATFORM}/envs",
                "args": [f"-var=spec_file=../customers/{name}/{env}.yaml"],
            }
    return stacks


def affected(paths: list[str], stacks: dict[str, dict]) -> list[dict]:
    """Stacks affected by the changed paths, in a stable order."""
    hit: set[str] = set()

    def prefixed(prefix: str) -> set[str]:
        return {k for k in stacks if k.startswith(prefix)}

    for path in paths:
        if path.startswith(EVERYTHING):
            hit |= set(stacks)
        elif path.startswith(f"{PLATFORM}/bootstrap/") or path.startswith(
            f"{PLATFORM}/modules/github-oidc-role/"
        ):
            hit |= prefixed("bootstrap")
            if path.startswith(f"{PLATFORM}/modules/github-oidc-role/"):
                hit |= prefixed("customers/")  # per-env deploy roles reuse it
        elif path.startswith(f"{PLATFORM}/ops-core/") or path.startswith(
            f"{PLATFORM}/modules/ops-core/"
        ):
            hit |= prefixed("ops-core")
        elif path.startswith("ops/agents/") and path.endswith(".md") and not path.endswith("README.md"):
            hit |= prefixed("ops-core")  # agents' IAM policies are built from these files
        elif path.startswith(f"{PLATFORM}/modules/lambda-function/"):
            hit |= prefixed("shared/") | prefixed("customers/") | prefixed("ops-core")  # all create functions
        elif path.startswith(
            (
                f"{PLATFORM}/shared/",
                f"{PLATFORM}/modules/network/",
                f"{PLATFORM}/modules/cache/",
                f"{PLATFORM}/modules/vendor-mock/",
            )
        ):
            hit |= prefixed("shared/")
        elif path.startswith((f"{PLATFORM}/envs/", f"{PLATFORM}/modules/streaming-service/")):
            hit |= prefixed("customers/")
        elif path.startswith(f"{PLATFORM}/customers/") and path.endswith(".yaml"):
            parts = Path(path).parts  # platform/customers/<name>/<env>.yaml
            if len(parts) == 4:
                key = f"customers/{parts[2]}/{Path(parts[3]).stem}"
                if key in stacks:
                    hit.add(key)
    return [stacks[k] for k in sorted(hit)]


def main() -> int:
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(json.dumps(affected(paths, discover())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
