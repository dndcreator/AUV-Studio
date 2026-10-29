from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, timeout: int) -> dict:
    started = time.monotonic()
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    child = subprocess.Popen(command, cwd=cwd, **kwargs)
    try:
        code = child.wait(timeout=timeout)
        return {"command": command, "exit_code": code, "duration_seconds": round(time.monotonic() - started, 3)}
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        return {"command": command, "exit_code": 124, "failure_type": "timeout", "duration_seconds": round(time.monotonic() - started, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline engineering gates; never invokes real model evaluation.")
    parser.add_argument("--layer", choices=("unit", "integration", "system", "frontend", "all"), default="all")
    parser.add_argument("--timeout", type=int, default=180, help="Hard wall timeout per subprocess")
    args = parser.parse_args(argv)
    if not 10 <= args.timeout <= 1800:
        parser.error("timeout must be10..1800 seconds")
    commands = []
    if args.layer != "frontend":
        commands.append(([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--durations=5",
                          *(["-m", args.layer] if args.layer != "all" else [])], ROOT / "backend"))
    if args.layer in {"all", "frontend"}:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            print("npm is required for the frontend gate")
            return 2
        commands.extend([([npm, "run", "test:run"], ROOT / "frontend"), ([npm, "run", "build"], ROOT / "frontend")])
    report = {"layer": args.layer, "paid_model_calls": 0, "steps": []}
    for command, cwd in commands:
        step = run(command, cwd, args.timeout)
        report["steps"].append(step)
        if step["exit_code"]:
            break
    path = ROOT / "backend" / "data" / "verification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Verification: {path}")
    return 0 if report["steps"] and all(step["exit_code"] == 0 for step in report["steps"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
