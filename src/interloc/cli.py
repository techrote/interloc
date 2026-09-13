"""Interlocuator command-line entry point."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from . import __version__
from .config import AppConfig
from .platforms import runtime_summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="interloc",
        description="Interlocuator local broker/courier control surface.",
        epilog="Run documented terminal commands separately unless a guide explicitly says otherwise.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    doctor = sub.add_parser("doctor", help="report local prerequisites without changing local state")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable UTF-8 JSON")
    return parser


def _doctor(as_json: bool) -> int:
    try:
        config = AppConfig.defaults()
    except (RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": {"code": "CONFIG_INVALID", "message": str(exc)}}
        print(json.dumps(payload, ensure_ascii=False) if as_json else f"CONFIG_INVALID: {exc}")
        return 2

    payload = {
        "ok": True,
        "version": __version__,
        "local_root": str(config.local_root),
        "local_root_exists": config.local_root.exists(),
        **runtime_summary(),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Interlocuator {__version__}")
        print(f"local root: {payload['local_root']} (exists={payload['local_root_exists']})")
        for provider in payload["providers"]:
            print(f"provider {provider['name']}: {provider['status']} — {provider['detail']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.json)
    parser.print_help()
    return 0
