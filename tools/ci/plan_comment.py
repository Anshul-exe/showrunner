"""Turn `terraform show -no-color <plan>` output into a PR comment body (stdin -> stdout).

Sensitive values are already masked by Terraform in this text form. The JSON form is
NOT masked (it holds variable values), so CI never uploads or prints it.
"""

from __future__ import annotations

import re
import sys

LIMIT = 60000  # GitHub comment limit is 65,536 characters


def summarise(stack: str, text: str, policy_ok: bool) -> str:
    m = re.search(r"Plan: (\d+) to add, (\d+) to change, (\d+) to destroy", text)
    if "No changes." in text:
        headline = "no changes"
    elif m:
        headline = f"{m.group(1)} to add, {m.group(2)} to change, **{m.group(3)} to destroy**"
    else:
        headline = "see details"
    body = text if len(text) <= LIMIT else text[:LIMIT] + "\n... (truncated; full plan in the job log)"
    policy = "passed" if policy_ok else "**FAILED** (see the job log)"
    return (
        f"<!-- sr-plan:{stack} -->\n"
        f"### `{stack}`: {headline}\n"
        f"Policy checks (never-create list, tags): {policy}\n\n"
        f"<details><summary>terraform plan</summary>\n\n```\n{body}\n```\n</details>\n"
    )


if __name__ == "__main__":
    print(summarise(sys.argv[1], sys.stdin.read(), sys.argv[2] == "ok"))
