from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("auv_launcher", Path(__file__).resolve().parents[2] / "run.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize(("version", "supported"), [
    ((18, 19, 0), False), ((20, 18, 9), False), ((20, 19, 0), True),
    ((21, 9, 0), False), ((22, 11, 0), False), ((22, 12, 0), True), ((24, 0, 0), True),
])
def test_node_version_matches_frontend_engine_requirement(version, supported) -> None:
    assert launcher._supported_node(version) is supported


@pytest.mark.parametrize("failure", ["frontend_spawn", "service_exit", "interrupt"])
def test_launcher_always_cleans_up_started_services(monkeypatch, failure) -> None:
    class Process:
        def poll(self):
            return 1 if failure == "service_exit" else None

    spawned = []
    cleaned = []
    opened = []

    def spawn(*_args, **_kwargs):
        if spawned and failure == "frontend_spawn":
            raise OSError("frontend unavailable")
        process = Process()
        spawned.append(process)
        return process

    def sleep(_seconds):
        if failure == "interrupt":
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "_npm_command", lambda: "npm")
    monkeypatch.setattr(launcher, "_stop_process", cleaned.append)
    monkeypatch.setattr(launcher.time, "sleep", sleep)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    if failure == "interrupt":
        launcher.start()
    else:
        with pytest.raises((OSError, SystemExit)):
            launcher.start()
    assert cleaned == list(reversed(spawned))
    assert opened == []
