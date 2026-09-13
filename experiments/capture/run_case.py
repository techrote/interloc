from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if os.name != "nt":
    raise SystemExit("run_case.py is Windows-only")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
MAX_BMP_BYTES = 16 * 1024 * 1024 + 4096


def outside_repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    raise ValueError("capture evidence must be written outside the source worktree")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded IL-012 capture case")
    parser.add_argument("--backend", choices=("printwindow", "wgc"), required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--hwnd", type=lambda value: int(value, 0))
    target.add_argument("--title-exact")
    parser.add_argument("--expect-pid", type=int)
    parser.add_argument("--client-only", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--record")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--state-label", required=True)
    parser.add_argument("--timeout-ms", type=int, default=10000)
    args = parser.parse_args()

    if not 1 <= args.timeout_ms <= 10000:
        raise ValueError("timeout must be in 1..10000 ms")

    output = outside_repo(Path(args.out))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    if args.backend == "printwindow":
        command = [sys.executable, str(HERE / "printwindow_probe.py")]
        if args.hwnd is not None:
            command += ["--hwnd", hex(args.hwnd)]
        else:
            command += ["--title-exact", args.title_exact]
        if args.expect_pid is not None:
            command += ["--expect-pid", str(args.expect_pid)]
        if args.client_only:
            command.append("--client-only")
        command += ["--out", str(output)]
    else:
        if args.hwnd is None or args.expect_pid is None:
            raise ValueError("WGC requires both --hwnd and --expect-pid")
        if args.title_exact is not None or args.client_only:
            raise ValueError("WGC prototype accepts explicit HWND only and captures the target surface")
        executable = HERE / "wgc_probe.exe"
        if not executable.is_file():
            raise FileNotFoundError("wgc_probe.exe is absent; build the prototype first")
        command = [
            str(executable),
            hex(args.hwnd),
            str(args.expect_pid),
            str(output),
            str(args.timeout_ms),
        ]

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(HERE),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout_ms / 1000.0 + 0.5,
            check=False,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = None
        timed_out = True
        child_stdout = (exc.stdout or "")[-4096:]
        child_stderr = (exc.stderr or "")[-4096:]
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if completed is not None:
        child_stdout = completed.stdout[-4096:]
        child_stderr = completed.stderr[-4096:]
        returncode = completed.returncode
    else:
        returncode = None

    payload = None
    if output.is_file():
        size = output.stat().st_size
        if size > MAX_BMP_BYTES:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"capture exceeded local experiment bound: {size} bytes")
        payload = output.read_bytes()

    child_result = None
    if child_stdout.strip():
        try:
            child_result = json.loads(child_stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            child_result = None

    record = {
        "case_id": args.case_id,
        "backend": args.backend,
        "state_label": args.state_label,
        "timeout_ms": args.timeout_ms,
        "timed_out": timed_out,
        "returncode": returncode,
        "wall_ms": round(elapsed_ms, 3),
        "output_name": output.name if payload is not None else None,
        "output_bytes": len(payload) if payload is not None else None,
        "output_sha256": hashlib.sha256(payload).hexdigest() if payload is not None else None,
        "child_result": child_result,
        "stderr_tail": child_stderr,
    }

    encoded = json.dumps(record, ensure_ascii=True, indent=2, sort_keys=True)
    if args.record:
        record_path = outside_repo(Path(args.record))
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)

    if timed_out or returncode != 0 or payload is None:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        raise SystemExit(2)
