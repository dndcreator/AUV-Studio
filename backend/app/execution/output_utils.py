from __future__ import annotations

from typing import Any

def output_text(output: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["content", "text", "summary", "result"]:
        value = output.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif value is not None and key == "result":
            parts.append(str(value))
    return "\n".join(parts).strip()
