from __future__ import annotations

from typing import Any


def normalize_plan_phases(raw: Any, task: dict[str, Any], phase_logic: list[str]) -> list[dict[str, str]]:
    phases = []
    for index, row in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(row, dict) or not str(row.get("objective", "")).strip():
            continue
        phases.append({"id": f"phase_{index + 1}", "name": str(row.get("name") or f"Phase {index + 1}"),
                       "objective": str(row["objective"]).strip(),
                       "exit_criteria": str(row.get("exit_criteria") or task["completion_condition"])})
    if not phases:
        phases = [{"id": f"phase_{index + 1}", "name": f"Phase {index + 1}", "objective": objective,
                   "exit_criteria": task["completion_condition"] if index == len(phase_logic) - 1 else "The phase objective is resolved."}
                  for index, objective in enumerate(phase_logic)]
    return phases[:12] or [{"id": "phase_1", "name": "Execution", "objective": task["objective"],
                           "exit_criteria": task["completion_condition"]}]
