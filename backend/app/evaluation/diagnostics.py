from __future__ import annotations

from typing import Any

from ..schemas import RunEvent


def check_context_summary(events: list[RunEvent]) -> dict[str, Any]:
    checks = [event.payload for event in events if event.event == "action_checked"]
    measured = [item for item in checks if isinstance(item.get("evidence_metrics"), dict)
                and all(type(item["evidence_metrics"].get(key)) is int
                        for key in ("estimated_input_tokens", "unprojected_input_tokens", "aliases"))]
    blocks: dict[str, dict[str, int]] = {}
    usage: dict[str, int] = {}
    for item in measured:
        for name, block in item["evidence_metrics"].get("blocks", {}).items():
            total = blocks.setdefault(name, {"chars": 0, "estimated_tokens": 0})
            for key in total:
                total[key] += block.get(key, 0)
        for name, value in item.get("provider_usage", {}).items():
            usage[name] = usage.get(name, 0) + value
    return {
        "measured_checks": len(measured), "check_records": len(checks),
        "estimated_input_tokens": sum(item["evidence_metrics"]["estimated_input_tokens"] for item in measured),
        "unprojected_input_tokens": sum(item["evidence_metrics"]["unprojected_input_tokens"] for item in measured),
        "aliases": sum(item["evidence_metrics"]["aliases"] for item in measured),
        "blocks": blocks, "provider_usage": usage,
    }


def round_audit_summary(events: list[RunEvent]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if event.event != "episode_audited":
            continue
        payload = event.payload
        progress = payload.get("progress_assessment", {})
        rows.append({
            "event_seq": event.seq, "round": payload.get("round"), "decision": payload.get("decision"),
            "task_complete": payload.get("task_complete"),
            "stagnation": progress.get("stagnation"), "goal_stagnation": progress.get("goal_stagnation"),
            "unassessed_goal_rounds": progress.get("unassessed_goal_rounds", []),
            "round_progress": [{key: item.get(key) for key in ("round", "stagnant", "goal_progress")}
                               for item in progress.get("round_progress", [])],
        })
    return rows
