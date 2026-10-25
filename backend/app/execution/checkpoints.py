from __future__ import annotations

import copy
from typing import Any

from ..schemas import RunRequest, WorkflowEdge, WorkflowNode
from .simulation_state import normalize_simulation_state


def build_context_snapshot(
    *,
    workflow_id: str,
    node: WorkflowNode,
    runtime_input: dict[str, Any],
    active_incoming_edges: list[WorkflowEdge],
    global_guidance: str,
) -> dict[str, Any]:
    interactions = runtime_input.get("_interactions", [])
    simulation = runtime_input.get("_simulation", {})
    simulation_phase = ""
    if isinstance(simulation, dict):
        phases = simulation.get("phases", [])
        index = int(simulation.get("current_phase_index", 0) or 0)
        if isinstance(phases, list) and 0 <= index < len(phases) and isinstance(phases[index], dict):
            simulation_phase = str(phases[index].get("name", ""))
    context_pack = runtime_input.get("_context_pack", {})
    return {
        "workflow_id": workflow_id,
        "node_id": node.id,
        "node_type": node.type,
        "upstream_nodes": [edge.source for edge in active_incoming_edges],
        "interaction_count": len(interactions) if isinstance(interactions, list) else 0,
        "has_memory": bool(runtime_input.get("_memory")),
        "has_global_guidance": bool(global_guidance),
        "simulation_phase": simulation_phase,
        "world_state": simulation.get("world_state", {})
        if isinstance(simulation, dict) and isinstance(simulation.get("world_state", {}), dict)
        else {},
        "context_entries": [
            str(entry.get("id", ""))
            for entry in context_pack.get("entries", [])
            if isinstance(entry, dict)
        ]
        if isinstance(context_pack, dict)
        else [],
    }


def runtime_context_pack_snapshot(runtime_input: dict[str, Any]) -> dict[str, Any]:
    pack = runtime_input.get("_context_pack", {})
    if not isinstance(pack, dict):
        return {"context_entries": []}
    entries = pack.get("entries", [])
    return {
        "episode": pack.get("episode", 0),
        "context_entries": [
            {
                "id": str(entry.get("id", "")),
                "title": str(entry.get("title", "")),
                "kind": str(entry.get("kind", "background")),
                "activation_reasons": entry.get("activation_reasons", []),
            }
            for entry in entries
            if isinstance(entry, dict)
        ]
        if isinstance(entries, list)
        else [],
    }


def continuous_snapshot(
    context: dict[str, Any],
    actions: list[dict[str, Any]],
    round_index: int,
    action_index: int,
    simulation_seq: int,
) -> dict[str, Any]:
    return {
        "simulation_state": context.get("_simulation", {}),
        "actions": actions,
        "round": round_index,
        "action_index": action_index,
        "simulation_seq": simulation_seq,
        "scene_control": copy.deepcopy(context.get("_scene_control", {})),
    }


def restore_continuous_checkpoint(context: dict[str, Any], request: RunRequest) -> None:
    rollback = request.input.get("_rollback") if isinstance(request.input, dict) else None
    snapshot = rollback.get("continuous_snapshot") if isinstance(rollback, dict) else None
    if not isinstance(snapshot, dict):
        return
    simulation_state = snapshot.get("simulation_state")
    if isinstance(simulation_state, dict):
        context["_simulation"] = normalize_simulation_state(simulation_state)
    if isinstance(snapshot.get("scene_control"), dict):
        context["_scene_control"] = copy.deepcopy(snapshot["scene_control"])
    context["_continuous_resume"] = {
        "actions": snapshot.get("actions", []) if isinstance(snapshot.get("actions"), list) else [],
        "round": int(snapshot.get("round", 0) or 0),
        "action_index": int(snapshot.get("action_index", 0) or 0),
        "simulation_seq": int(snapshot.get("simulation_seq", 0) or 0),
    }
