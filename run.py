from __future__ import annotations

import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
MIN_NODE = (20, 19, 0)


def fail(message: str) -> None:
    print(f"\n[ERROR] {message}")
    print("\nPlease prepare dependencies first, then run this launcher again.")
    sys.exit(1)


def _parse_semver(raw: str) -> tuple[int, int, int] | None:
    text = raw.strip().lstrip("v")
    parts = text.split(".")
    if len(parts) < 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return major, minor, patch


def _check_node_version() -> None:
    node = shutil.which("node")
    if node is None:
        fail("Node.js was not found in PATH. Install Node.js 20.19+ or 22.12+.")
    try:
        result = subprocess.run([node, "--version"], check=True, capture_output=True, text=True)
    except Exception as exc:  # noqa: BLE001
        fail(f"failed to check Node.js version: {exc}")
    version = _parse_semver(result.stdout)
    if version is None:
        fail(f"could not parse Node.js version: {result.stdout.strip()}")
    if version < MIN_NODE:
        fail(
            "Node.js is too old for the current frontend stack. "
            f"Found {result.stdout.strip()}, required v20.19.0+ or v22.12.0+. "
            "Upgrade Node.js, then run: cd frontend && npm install"
        )


def _npm_command() -> str:
    if sys.platform.startswith("win"):
        npm_cmd = shutil.which("npm.cmd")
        if npm_cmd:
            return npm_cmd
    npm = shutil.which("npm")
    if npm:
        return npm
    fail("npm was not found in PATH.")


def check_ready() -> None:
    if not BACKEND.exists():
        fail("backend directory not found.")
    if not FRONTEND.exists():
        fail("frontend directory not found.")
    if not (BACKEND / "app" / "main.py").exists():
        fail("backend/app/main.py not found.")
    if not (FRONTEND / "package.json").exists():
        fail("frontend/package.json not found.")
    if not (FRONTEND / "node_modules").exists():
        fail("frontend dependencies not found. Run: cd frontend && npm install")
    _npm_command()
    _check_node_version()

    try:
        import fastapi  # noqa: F401
        import sqlalchemy  # noqa: F401
        import uvicorn  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        fail(f"backend Python dependencies are not ready: {exc}")


def start() -> None:
    print("AUV launcher")
    print(f"Root: {ROOT}")
    print("\nStarting backend:  http://localhost:8000")
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
        cwd=BACKEND,
    )

    print("Starting frontend: http://localhost:5173")
    frontend_proc = subprocess.Popen(
        [_npm_command(), "run", "dev"],
        cwd=FRONTEND,
    )

    time.sleep(2)
    webbrowser.open("http://localhost:5173")

    print("\nAUV is running. Press Ctrl+C here to stop both services.")
    try:
        while True:
            if backend_proc.poll() is not None:
                fail("backend process exited.")
            if frontend_proc.poll() is not None:
                fail("frontend process exited.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping AUV...")
        backend_proc.terminate()
        frontend_proc.terminate()


if __name__ == "__main__":
    check_ready()
    start()
