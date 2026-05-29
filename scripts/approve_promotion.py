#!/usr/bin/env python3
"""CLI for reviewing and approving Dream Lab promotion proposals."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.live_policy import policy_summary, rollback  # noqa: E402
from rd import promotions  # noqa: E402


def cmd_list(args: argparse.Namespace) -> int:
    rows = promotions.list_promotions(status=args.status, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No promotions found.")
        return 0
    for row in rows:
        print(
            f"{row.get('id')}  [{row.get('status')}]  {row.get('type')}  "
            f"risk={row.get('risk')}  agent={row.get('agent')}"
        )
        print(f"  {row.get('summary')}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    row = promotions.get_promotion(args.promo_id)
    if not row:
        print(f"Promotion not found: {args.promo_id}", file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    try:
        result = promotions.approve(args.promo_id, actor=args.actor)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    try:
        row = promotions.reject(args.promo_id, reason=args.reason, actor=args.actor)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2))
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    print(json.dumps(policy_summary(), indent=2))
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    restored = rollback(args.version)
    if not restored:
        print("Rollback failed: no matching policy version.", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "version": restored.get("version")}, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dream Lab promotion approval CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List promotion proposals")
    list_p.add_argument("--status", default=None, help="Filter by status (pending, approved, rejected)")
    list_p.add_argument("--limit", type=int, default=50)
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=cmd_list)

    show_p = sub.add_parser("show", help="Show one promotion")
    show_p.add_argument("promo_id")
    show_p.set_defaults(func=cmd_show)

    approve_p = sub.add_parser("approve", help="Approve and apply a promotion")
    approve_p.add_argument("promo_id")
    approve_p.add_argument("--actor", default="cli")
    approve_p.set_defaults(func=cmd_approve)

    reject_p = sub.add_parser("reject", help="Reject a promotion")
    reject_p.add_argument("promo_id")
    reject_p.add_argument("--reason", default="")
    reject_p.add_argument("--actor", default="cli")
    reject_p.set_defaults(func=cmd_reject)

    policy_p = sub.add_parser("policy", help="Show current live policy summary")
    policy_p.set_defaults(func=cmd_policy)

    rollback_p = sub.add_parser("rollback", help="Rollback live policy to a prior version")
    rollback_p.add_argument("--version", type=int, default=None)
    rollback_p.set_defaults(func=cmd_rollback)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
