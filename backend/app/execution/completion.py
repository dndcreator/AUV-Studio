from __future__ import annotations

from typing import Any


def execution_allowance(provider: Any, policy: Any, round_index: int, action_index: int) -> dict[str, Any]:
    remaining = getattr(provider, "remaining_budget", None)
    return {"remaining_rounds": max(0, policy.max_rounds - round_index),
            "remaining_actions": max(0, policy.max_actions - action_index),
            **(remaining() if callable(remaining) else {})}


def goal_completed(control: dict[str, Any]) -> bool:
    reason = control.get("completion_reason")
    return (control.get("decision") == "stop" and control.get("task_complete") is True
            and isinstance(reason, str) and bool(reason.strip()))


def completion_result(control: dict[str, Any], *, action_count: int, action_limit: int) -> dict[str, Any]:
    completed = goal_completed(control)
    reason = ("goal_completed" if completed else "director_stopped" if control.get("decision") == "stop"
              else "action_limit" if action_count >= action_limit else "round_limit")
    return {"status": "completed" if completed else "incomplete", "reason": reason,
            "assessment": str(control.get("completion_reason") or control.get("reason") or "")}
