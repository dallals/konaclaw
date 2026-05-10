"""kc-supervisor CLI entrypoint.

Currently supports:
    python -m kc_supervisor channel-routing add    --db <path> <channel> <chat_id>
    python -m kc_supervisor channel-routing list   --db <path>
    python -m kc_supervisor channel-routing disable --db <path> <channel>

The supervisor is normally launched via the ``kc-supervisor`` script entry point
(``kc_supervisor.main:main``), which starts FastAPI+uvicorn. This __main__.py
is a separate admin CLI and does NOT start the server.
"""
from __future__ import annotations
import argparse
import sys
from kc_supervisor.storage import Storage


def _cmd_channel_routing_add(args: argparse.Namespace) -> int:
    Storage(args.db).upsert_channel_routing(args.channel, args.default_chat_id, enabled=1)
    print(f"OK: {args.channel} -> {args.default_chat_id} (enabled)")
    return 0


def _cmd_channel_routing_list(args: argparse.Namespace) -> int:
    rows = Storage(args.db).list_channel_routing()
    if not rows:
        print("(no routing entries)")
        return 0
    for r in rows:
        flag = "enabled" if r["enabled"] else "disabled"
        print(f"{r['channel']:12s} {r['default_chat_id']:20s} {flag}")
    return 0


def _cmd_channel_routing_disable(args: argparse.Namespace) -> int:
    s = Storage(args.db)
    cur = s.get_channel_routing(args.channel)
    if cur is None:
        print(f"ERROR: no routing entry for {args.channel!r}", file=sys.stderr)
        return 2
    s.upsert_channel_routing(args.channel, cur["default_chat_id"], enabled=0)
    print(f"OK: {args.channel} disabled")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m kc_supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cr = subparsers.add_parser(
        "channel-routing",
        help="Manage cross-channel allowlist for scheduled reminders",
    )
    cr_sub = cr.add_subparsers(dest="cr_action", required=True)

    add = cr_sub.add_parser("add", help="Add or replace a routing entry")
    add.add_argument("--db", required=True)
    add.add_argument("channel", choices=["telegram", "dashboard", "imessage"])
    add.add_argument("default_chat_id")
    add.set_defaults(func=_cmd_channel_routing_add)

    lst = cr_sub.add_parser("list", help="List routing entries")
    lst.add_argument("--db", required=True)
    lst.set_defaults(func=_cmd_channel_routing_list)

    dis = cr_sub.add_parser("disable", help="Disable a routing entry without deleting it")
    dis.add_argument("--db", required=True)
    dis.add_argument("channel")
    dis.set_defaults(func=_cmd_channel_routing_disable)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
