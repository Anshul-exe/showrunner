"""sr: the Showrunner operator CLI.

    ./sr status [--env dev]
    ./sr config show <customer> <flags|chaos>
    ./sr config set  <customer> <flags|chaos> key=value [key=value...]
    ./sr demo traffic [--minutes 10] [--rps 2] [--customer c ...]
    ./sr demo watch [--once]
    ./sr demo s1 start|stop
    ./sr deploy <catalog-api|vendor-mock|incident>
    ./sr snapshot --since 22:00 --until 22:20 --out docs/demo/s1   (UTC, today)

Uses the `showrunner` AWS profile (pinned in .claude/settings.json) and refuses to run
against any other account. Never prints secrets: it only reads names, IDs, metrics and
runtime config.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appconfig  # noqa: E402
import common  # noqa: E402
import deploy  # noqa: E402
import snapshot  # noqa: E402
import scenarios  # noqa: E402
import traffic  # noqa: E402
import watch  # noqa: E402


def cmd_status(args) -> int:
    watch.watch(common.customers(args.env), args.env, once=True)
    for c in common.customers(args.env):
        flags, _ = appconfig.deployed(c, args.env, "flags")
        chaos, _ = appconfig.deployed(c, args.env, "chaos")
        print(f"  {c}: flags={json.dumps(flags)} chaos={json.dumps(chaos)}")
    return 0


def cmd_config(args) -> int:
    if args.action == "show":
        content, version = appconfig.deployed(args.customer, args.env, args.profile)
        print(json.dumps({"deployed_version": version, "content": content}, indent=2))
        return 0
    changes = {}
    for kv in args.pairs:
        if "=" not in kv:
            common.die(f"expected key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        changes[k] = appconfig.parse_value(v)
    result = appconfig.set_keys(args.customer, args.env, args.profile, changes, f"sr config set by operator: {changes}")
    print(json.dumps(result, indent=2))
    return 0 if result["state"] in ("BAKING", "COMPLETE") else 1


def cmd_demo(args) -> int:
    if args.env != "dev":
        common.die("demos run against dev only (CLAUDE.md rule 7)")
    customers = args.customer or common.customers(args.env)
    if args.demo == "traffic":
        traffic.run(customers, args.env, args.minutes, args.rps)
    elif args.demo == "watch":
        watch.watch(customers, args.env, once=args.once)
    elif args.demo == "s1":
        return scenarios.s1(args.action, args.env)
    return 0


def cmd_snapshot(args) -> int:
    from datetime import datetime, timezone
    from pathlib import Path

    def parse(t: str) -> datetime:
        if "T" in t or "-" in t:
            return datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        h, m = (int(x) for x in t.split(":"))
        return datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)

    for path in snapshot.snapshot(args.env, parse(args.since), parse(args.until), Path(args.out), args.only):
        print(path)
    return 0


def cmd_deploy(args) -> int:
    return deploy.deploy(args.service, args.env)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default="dev", choices=["dev", "prod"])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="alarms, dependency p95, flags and chaos")

    c = sub.add_parser("config", help="show or change runtime config (AppConfig, lane 1)")
    c.add_argument("action", choices=["show", "set"])
    c.add_argument("customer")
    c.add_argument("profile", choices=["flags", "chaos"])
    c.add_argument("pairs", nargs="*")

    d = sub.add_parser("demo", help="demo helpers (dev only)")
    d.add_argument("demo", choices=["traffic", "watch", "s1"])
    d.add_argument("action", nargs="?", choices=["start", "stop"])
    d.add_argument("--minutes", type=float, default=10)
    d.add_argument("--rps", type=float, default=2)
    d.add_argument("--customer", action="append")
    d.add_argument("--once", action="store_true")

    dp = sub.add_parser("deploy", help="ship app code (lane 2): new version, alias live")
    dp.add_argument("service", choices=["catalog-api", "vendor-mock", "incident"])

    sp = sub.add_parser("snapshot", help="render dashboard widgets to PNG for a time window (UTC)")
    sp.add_argument("--since", required=True, help="HH:MM today or ISO time, UTC")
    sp.add_argument("--until", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--only", help="only widgets whose title contains this")

    args = p.parse_args(argv)
    common.check_account()
    return {"status": cmd_status, "config": cmd_config, "demo": cmd_demo, "deploy": cmd_deploy, "snapshot": cmd_snapshot}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
