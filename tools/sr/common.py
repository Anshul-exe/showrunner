"""Shared helpers for sr: the account guard, SSM lookups, AWS clients."""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
REGION = os.environ.get("AWS_REGION", "us-east-1")
CUSTOMERS_DIR = ROOT / "platform" / "customers"


def die(msg: str, code: int = 1) -> None:
    print(f"sr: {msg}", file=sys.stderr)
    raise SystemExit(code)


@functools.cache
def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


def client(name: str):
    return session().client(name)


@functools.cache
def check_account() -> str:
    """Stop if the credentials aren't for the Showrunner account (CLAUDE.md rule 1)."""
    expected = os.environ.get("AWS_ACCOUNT_ID") or (ROOT / ".showrunner-account").read_text().strip()
    actual = client("sts").get_caller_identity()["Account"]
    if actual != expected:
        die(f"credentials are for account {actual}, expected {expected}", 2)
    return actual


def customers(env: str) -> list[str]:
    return sorted(p.parent.name for p in CUSTOMERS_DIR.glob(f"*/{env}.yaml"))


@functools.cache
def params(path: str) -> dict[str, str]:
    """All SSM parameters under a path, keyed by their last segment."""
    out: dict[str, str] = {}
    for page in client("ssm").get_paginator("get_parameters_by_path").paginate(Path=path):
        for p in page["Parameters"]:
            out[p["Name"].rsplit("/", 1)[1]] = p["Value"]
    if not out:
        die(f"no parameters under {path}: is that stack applied?")
    return out


def customer_params(customer: str, env: str) -> dict[str, str]:
    return params(f"/showrunner/{env}/customers/{customer}")


def shared_params(env: str) -> dict[str, str]:
    return params(f"/showrunner/{env}/shared")
