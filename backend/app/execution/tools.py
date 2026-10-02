from __future__ import annotations

from typing import Any


class ToolExecutionError(RuntimeError):
    pass


def _tool_echo(args: dict[str, Any]) -> dict[str, Any]:
    return {"text": str(args.get("text", ""))}


def _tool_concat(args: dict[str, Any]) -> dict[str, Any]:
    items = args.get("items", [])
    if not isinstance(items, list):
        raise ToolExecutionError("concat.items must be a list")
    return {"text": "".join(str(item) for item in items)}


def _tool_select(args: dict[str, Any]) -> dict[str, Any]:
    obj = args.get("object", {})
    path = str(args.get("path", ""))
    cur: Any = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise ToolExecutionError(f"path not found: {path}")
    return {"value": cur}


TOOLS = {
    "echo": _tool_echo,
    "concat": _tool_concat,
    "select": _tool_select,
}


def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise ToolExecutionError(f"tool '{name}' is not in whitelist")
    return TOOLS[name](args)
