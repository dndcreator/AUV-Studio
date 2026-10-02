from __future__ import annotations

from typing import Any

from bridge_schemas import BridgeTaskInput


def handle_task(task: BridgeTaskInput) -> dict[str, Any]:
    """
    Replace this function body with your own agent call.

    Minimal contract:
    - input: task.text + optional context/background
    - output: dict with key `text`
    """
    text = task.text.strip()
    if not text:
        text = "(empty task)"
    return {
        "text": f"[bridge-agent] {text}",
        "meta": {
            "memory_items": len(task.background.get("memory", [])) if isinstance(task.background, dict) else 0,
        },
    }
