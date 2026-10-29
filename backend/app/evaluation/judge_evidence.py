from __future__ import annotations

from typing import Any


def target_evidence(case: dict[str, Any], output: Any) -> dict[str, Any]:
    """Expose exact event/source IDs without duplicating the action log inside a second envelope."""
    payload = case["input"]
    if case["capability"] == "system":
        result = {"task": output["task"], "roles": output["roles"], "output": {
            key: value for key, value in output.items() if key not in {"output", "audits", "task", "roles", "coding_progress", "failed_checks"}}}
        result["output"].update({key: value for key, value in output["output"].items() if key not in {"actions", "final"}})
        for action in output["output"].get("actions", []):
            result[f"event.{action['event_seq']}"] = action
        for audit in output["audits"]:
            result[f"audit.{audit['seq']}"] = audit["payload"]
        return result
    if case["capability"] in {"action_check", "state_coder"}:
        evidence = {"task": {key: value for key, value in payload.items() if key not in {"evidence", "actions"}}, "output": output}
        for row in payload.get("evidence", []):
            evidence[f"source.{row['id']}"] = row.get("value")
        for row in payload.get("actions", []):
            evidence[f"event.{row['event_seq']}"] = row
        return evidence
    return {"task": payload, "output": output}
