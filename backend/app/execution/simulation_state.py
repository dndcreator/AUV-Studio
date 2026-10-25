from __future__ import annotations

import copy
from typing import Any

from ..schemas import WorkflowDefinition, WorkflowNode
from .dynamic_state import init_dynamic_state

STATE_SCHEMA_VERSION = 2
_WORLD_COORDINATE_KEYS = ("current_time", "current_location", "temporal_scope", "spatial_scope")


def _ratio(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


def _normalize_phases(raw: Any) -> list[dict[str, str]]:
    phases = raw if isinstance(raw, list) else []
    normalized = [
        {
            "id": str(phase.get("id", f"phase_{index + 1}")),
            "name": str(phase.get("name", f"Phase {index + 1}")),
            "objective": str(phase.get("objective", "")),
            "exit_criteria": str(phase.get("exit_criteria", "")),
        }
        for index, phase in enumerate(phases)
        if isinstance(phase, dict)
    ]
    return normalized or [{"id": "phase_1", "name": "Execution", "objective": "Run simulation steps and collect evidence.", "exit_criteria": "Core nodes complete execution."}]


def normalize_simulation_state(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    phases = _normalize_phases(source.get("phases"))
    metrics = source.get("runtime_metrics") if isinstance(source.get("runtime_metrics"), dict) else {}
    world = source.get("world_state") if isinstance(source.get("world_state"), dict) else {}
    dynamic = source.get("dynamic_state")
    if not isinstance(dynamic, dict):
        dynamic = init_dynamic_state({"enabled": False})
    phase_index = max(0, min(len(phases) - 1, int(source.get("current_phase_index", 0) or 0)))
    return {
        "version": STATE_SCHEMA_VERSION,
        "mode": str(source.get("mode", "simulation")),
        "detail_granularity": str(source.get("detail_granularity", "concise")),
        "research_question": str(source.get("research_question", "")),
        "task_contract": copy.deepcopy(source.get("task_contract")) if isinstance(source.get("task_contract"), dict) else {},
        "evidence": copy.deepcopy(source.get("evidence")) if isinstance(source.get("evidence"), dict) else {},
        "evidence_status": str(source.get("evidence_status", "none")),
        "phases": phases,
        "current_phase_index": phase_index,
        "episode_index": max(0, int(source.get("episode_index", 0) or 0)),
        "runtime_metrics": {"progress": _ratio(metrics.get("progress", 0.0))},
        "world_state": normalize_world_state(world),
        "dynamic_state": copy.deepcopy(dynamic),
    }


def init_simulation_state(workflow: WorkflowDefinition) -> dict[str, Any]:
    environment = workflow.environment.model_dump()
    raw = environment.get("simulation", {})
    if not isinstance(raw, dict):
        raw = {}
    normalized_phases = _normalize_phases(raw.get("phases"))
    runtime_metrics = raw.get("runtime_metrics", {}) if isinstance(raw.get("runtime_metrics"), dict) else {}
    world_state_raw = raw.get("world_state", {})
    if not isinstance(world_state_raw, dict):
        world_state_raw = {}
    dynamic_state_config = raw.get("dynamic_state", {})
    if not isinstance(dynamic_state_config, dict):
        dynamic_state_config = {}
    if "enabled" not in dynamic_state_config:
        dynamic_state_config = {
            **dynamic_state_config,
            "enabled": str(raw.get("execution_model", "")).strip().lower() == "continuous",
        }
    dynamic_state = init_dynamic_state(dynamic_state_config)
    state = {
        "version": STATE_SCHEMA_VERSION,
        "mode": str(raw.get("mode", "simulation")),
        "detail_granularity": str(raw.get("detail_granularity", "concise"))
        if str(raw.get("detail_granularity", "concise")).strip().lower() in {"concise", "detailed"}
        else "concise",
        "research_question": str(raw.get("research_question", "")),
        "task_contract": raw.get("task_contract", {}) if isinstance(raw.get("task_contract", {}), dict) else {},
        "evidence": raw.get("evidence", {}) if isinstance(raw.get("evidence", {}), dict) else {},
        "evidence_status": str(raw.get("evidence_status", "none")),
        "phases": normalized_phases,
        "current_phase_index": 0,
        "episode_index": 0,
        "runtime_metrics": {
            "progress": _ratio(runtime_metrics.get("progress", 0.0)),
        },
        "world_state": normalize_world_state({
            "current_time": str(world_state_raw.get("current_time", environment.get("time_context", ""))),
            "current_location": str(world_state_raw.get("current_location", environment.get("spatial_context", ""))),
            "temporal_scope": str(world_state_raw.get("temporal_scope", environment.get("time_context", ""))),
            "spatial_scope": str(world_state_raw.get("spatial_scope", environment.get("spatial_context", ""))),
        }),
        "dynamic_state": dynamic_state,
    }
    return normalize_simulation_state(state)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return default


def update_simulation_state(
    state: dict[str, Any],
    *,
    node: WorkflowNode,
    runtime_input: dict[str, Any],
    output: dict[str, Any] | None,
    status: str,
    seq: int,
) -> dict[str, Any]:
    state = normalize_simulation_state(state)
    del node, runtime_input, seq
    phases = state.get("phases", [])
    if not isinstance(phases, list):
        phases = []
    runtime_metrics = state.get("runtime_metrics", {})
    if not isinstance(runtime_metrics, dict):
        runtime_metrics = {}
    progress = safe_float(runtime_metrics.get("progress", 0.0))
    world_state = state.get("world_state", {})
    if not isinstance(world_state, dict):
        world_state = {}

    if status == "succeeded":
        progress += 0.12
    elif status == "failed":
        progress += 0.03
    elif status == "waiting_human":
        progress += 0.02
    else:
        progress += 0.01

    if isinstance(output, dict):
        sim_updates = output.get("simulation_updates", {})
        if isinstance(sim_updates, dict):
            vars_patch = sim_updates.get("runtime_metrics", {})
            if isinstance(vars_patch, dict):
                progress = safe_float(vars_patch.get("progress", progress), progress)
            world_patch = sim_updates.get("world_state", {})
            if isinstance(world_patch, dict):
                world_state = merge_world_state(world_state, world_patch)
    progress = max(0.0, min(1.0, progress))

    phase_count = max(1, len(phases))
    current_phase_index = min(phase_count - 1, int(progress * phase_count))
    state["current_phase_index"] = current_phase_index
    state["runtime_metrics"] = {"progress": round(progress, 4)}
    state["world_state"] = normalize_world_state(world_state)
    return state


def normalize_world_state(world_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_time": str(world_state.get("current_time", "")),
        "current_location": str(world_state.get("current_location", "")),
        "temporal_scope": str(world_state.get("temporal_scope", "")),
        "spatial_scope": str(world_state.get("spatial_scope", "")),
    }


def merge_world_state(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key in _WORLD_COORDINATE_KEYS:
        value = patch.get(key)
        if value not in {"", None}:
            merged[key] = str(value)
    return normalize_world_state(merged)
