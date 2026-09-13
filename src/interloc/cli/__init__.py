"""Local CLI: control and preview only; no automatic provider execution."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence

from interloc import __version__
from interloc.broker import BrokerError, Journal
from interloc.config import AppConfig
from interloc.platforms import runtime_summary
from interloc.policy import PolicyError
from interloc.protocol import ProtocolError
from .control import ControlError, ControlService, load_enrollment, read_local_file, validate_home


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # User-controlled argv may contain terminal escapes or personal paths.
        raise ControlError("ARGUMENT_INVALID", "Arguments are invalid. Run interloc --help.")


def _build_parser() -> argparse.ArgumentParser:
    parser = Parser(prog="interloc", description="Interlocuator local broker/courier control surface.",
                    epilog="Run terminal commands separately. No command sends to Chat or reads the clipboard.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--home", type=Path, help="absolute local state root outside project worktrees")
    parser.add_argument("--config", type=Path, help="explicit local enrollment JSON; never a mailbox request")
    sub = parser.add_subparsers(dest="command")
    doctor = sub.add_parser("doctor", help="inspect prerequisites without creating state")
    doctor.add_argument("--json", action="store_true")
    status = sub.add_parser("status", help="show bounded queue, running work and durable pause state")
    status.add_argument("--json", action="store_true")
    sub.add_parser("pause", help="stop new admission, execution and publication; running work may finish")
    sub.add_parser("resume", help="explicitly resume local admission; never approves requests")
    req = sub.add_parser("request", help="strict local import and request preview")
    reqsub = req.add_subparsers(dest="request_command", required=True)
    imp = reqsub.add_parser("import", help="read one local UTF-8 JSON file through shared policy and replay checks")
    imp.add_argument("path", type=Path)
    show = reqsub.add_parser("show", help="show exact identity, destination, effect and expiry")
    show.add_argument("request_id")
    for command, help_text in (("approve", "review then type the complete digest in an interactive terminal"),
                               ("deny", "durably deny unstarted work"), ("revoke", "revoke consent for unstarted work")):
        child = sub.add_parser(command, help=help_text)
        child.add_argument("request_id")
    return parser


def _emit(value: object) -> None:
    # Escaped Unicode/control characters are inert on every terminal/code page.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _error(exc: Exception) -> int:
    code = getattr(exc, "code", "LOCAL_IO_ERROR")
    causes = {
        "AUTHORITY_CHANGED": "Local enrollment changed; use a fresh request ID.",
        "TARGET_CHANGED": "The target changed after intake; review a fresh request.",
        "TARGET_UNAVAILABLE": "No qualified enrolled target provider is available.",
        "INTERACTIVE_REQUIRED": "Approval needs an interactive terminal and the full displayed digest.",
        "PAUSED": "Local admission, execution and publication are paused.",
        "REQUEST_NOT_FOUND": "This request is not in the current enrolled mailbox journal.",
        "BROKER_ALREADY_RUNNING": "The broker state is already owned by another process.",
        "QUEUE_FULL": "The pending request queue is full; inspect status before retrying.",
        "TOO_LATE": "Work has started; pause and reconcile completed effects.",
        "HOME_IN_WORKTREE": "Runtime data must live outside project worktrees.",
        "EXPIRED": "The request expired; create a fresh request.",
    }
    _emit({"ok": False, "error": {"code": code, "message": causes.get(code, "The local operation was rejected; no action was authorized."),
                                  "remedy": "Run interloc --help; inspect local enrollment and request status.",
                                  "evidence": "local-state:journal"}})
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    journal = None
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        if not args.command:
            parser.print_help()
            return 0
        config = AppConfig(local_root=args.home) if args.home else AppConfig.defaults()
        config.validate()
        home = validate_home(config.local_root)
        if args.command == "doctor":
            payload = {"ok": True, "version": __version__, "local_root": str(home),
                       "local_root_exists": home.exists(), "enrollment_exists": (home / "config/enrollment.json").is_file(),
                       "provider_execution": "not-wired", **runtime_summary()}
            if args.json:
                _emit(payload)
            else:
                print(f"Interlocuator {__version__}")
                _emit(payload)
            return 0
        state = home / "state"
        for candidate in (state, state / "journal.sqlite3", state / "journal.sqlite3-wal", state / "journal.sqlite3-shm"):
            if candidate.is_symlink() or candidate.is_junction():
                raise ControlError("HOME_INVALID", "State files cannot be links.")
        if args.command == "status" and not (state / "journal.sqlite3").exists():
            _emit({"ok": True, "initialized": False, "paused": False, "running": 0, "counts": {}, "requests": []})
            return 0
        # Pause/resume intentionally require neither credentials nor enrollment.
        if args.command in {"pause", "resume", "status"}:
            journal = Journal(state / "journal.sqlite3")
            if args.command != "status":
                journal.set_paused(args.command == "pause")
            _emit({"ok": True, "initialized": True, **journal.status()})
            return 0
        enrollment_path = args.config or home / "config/enrollment.json"
        # Validate configuration before creating runtime state.
        load_enrollment(enrollment_path)
        journal = Journal(state / "journal.sqlite3")
        service = ControlService(journal, lambda: load_enrollment(enrollment_path))
        if args.command == "request" and args.request_command == "import":
            key, state_name = service.admit(read_local_file(args.path))
            _emit({"ok": True, "request_id": key.request_id, "state": state_name,
                   "digest": journal.get(key)["input_sha256"], "source": "local_import"})
            return 0
        key = service.key(args.request_id)
        if args.command == "request":
            _emit({"ok": True, "preview": asdict(service.preview(key))})
        elif args.command == "approve":
            preview = service.preview(key)
            _emit({"preview": asdict(preview)})
            if not sys.stdin.isatty():
                raise ControlError("INTERACTIVE_REQUIRED", "Approval requires an interactive local terminal.")
            print("Type the complete 64-character request digest to approve, or press Enter to cancel:", flush=True)
            typed = sys.stdin.readline(130).strip()
            if not typed:
                _emit({"ok": True, "approved": False, "state": preview.state})
                return 0
            state_name = service.approve(key, preview=preview, typed_digest=typed)
            _emit({"ok": True, "request_id": key.request_id, "state": state_name, "executed": False})
        elif args.command in {"deny", "revoke"}:
            state_name = getattr(service, args.command)(key)
            _emit({"ok": True, "request_id": key.request_id, "state": state_name})
        return 0
    except KeyboardInterrupt:
        return _error(ControlError("INTERRUPTED", "Local operation interrupted."))
    except (ControlError, BrokerError, PolicyError, ProtocolError, OSError, ValueError, sqlite3.Error) as exc:
        return _error(exc)
    finally:
        if journal is not None:
            journal.close()
