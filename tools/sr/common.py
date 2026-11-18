"""Shared helpers for sr: the account guard, SSM lookups, AWS clients."""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

import boto3
import botocore.exceptions

ROOT = Path(__file__).resolve().parents[2]
REGION = os.environ.get("AWS_REGION", "us-east-1")
PROFILE = "showrunner"
CUSTOMERS_DIR = ROOT / "platform" / "customers"


def die(msg: str, code: int = 1) -> None:
    print(f"sr: {msg}", file=sys.stderr)
    raise SystemExit(code)


def profile_name(env: dict[str, str] = os.environ) -> str | None:
    """The `showrunner` profile unless the caller chose credentials explicitly.

    Without this, a shell with no AWS_PROFILE fell back to the default profile, which
    belongs to another account, and failed with InvalidClientTokenId (18/11/2026).
    Explicit choices win: AWS_PROFILE, or key env vars (CI's OIDC credentials).
    """
    if env.get("AWS_PROFILE"):
        return env["AWS_PROFILE"]
    if env.get("AWS_ACCESS_KEY_ID"):
        return None
    return PROFILE


@functools.cache
def session() -> boto3.Session:
    try:
        return boto3.Session(region_name=REGION, profile_name=profile_name())
    except botocore.exceptions.ProfileNotFound:
        die(f"no AWS profile {profile_name()!r}: run `aws configure --profile {PROFILE}` (or set AWS_PROFILE)", 2)


def client(name: str):
    return session().client(name)


@functools.cache
def check_account() -> str:
    """Stop if the credentials aren't for the Showrunner account (CLAUDE.md rule 1)."""
    pin = ROOT / ".showrunner-account"
    expected = os.environ.get("AWS_ACCOUNT_ID") or (pin.read_text().strip() if pin.exists() else "")
    if not expected:
        die("no account pin: create .showrunner-account (or set AWS_ACCOUNT_ID)", 2)
    who = f"profile {profile_name()!r}" if profile_name() else "credentials from the environment"
    try:
        actual = client("sts").get_caller_identity()["Account"]
    except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code") or type(exc).__name__
        die(f"{who} can't sign in to AWS ({code}); expected account {expected}. "
            f"Try: export AWS_PROFILE={PROFILE} AWS_REGION={REGION}", 2)
    if actual != expected:
        die(f"{who} is for account {actual}, not {expected} (.showrunner-account); "
            f"export AWS_PROFILE={PROFILE}", 2)
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
