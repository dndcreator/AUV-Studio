from __future__ import annotations

from typing import Any

from ..schemas import WorkflowDefinition, WorkflowNode
from .output_utils import output_text
def init_simulation_state(workflow: WorkflowDefinition) -> dict[str, Any]:
    environment = workflow.environment.model_dump()
    raw = environment.get("simulation", {})
    if not isinstance(raw, dict):
        raw = {}
    phases = raw.get("phases", [])
    if not isinstance(phases, list):
        phases = []
    normalized_phases: list[dict[str, Any]] = []
    for i, phase in enumerate(phases):
        if not isinstance(phase, dict):
            continue
        normalized_phases.append(
            {
                "id": str(phase.get("id", f"phase_{i+1}")),
                "name": str(phase.get("name", f"Phase {i+1}")),
                "objective": str(phase.get("objective", "")),
                "exit_criteria": str(phase.get("exit_criteria", "")),
            }
        )
    if not normalized_phases:
        normalized_phases = [
            {
                "id": "phase_1",
                "name": "Execution",
                "objective": "Run simulation steps and collect evidence.",
                "exit_criteria": "Core nodes complete execution.",
            }
        ]
    vars_raw = raw.get("variables", {})
    if not isinstance(vars_raw, dict):
        vars_raw = {}
    variables = {
        "progress": float(vars_raw.get("progress", 0.0)),
        "confidence": float(vars_raw.get("confidence", 0.5)),
        "risk": float(vars_raw.get("risk", 0.3)),
        "alignment": float(vars_raw.get("alignment", 0.5)),
    }
    for k in list(variables.keys()):
        variables[k] = max(0.0, min(1.0, float(variables[k])))
    state_memory_raw = raw.get("state_memory", {})
    if not isinstance(state_memory_raw, dict):
        state_memory_raw = {}
    current_states_raw = state_memory_raw.get("current_states", {})
    if not isinstance(current_states_raw, dict):
        current_states_raw = {}
    transitions_raw = state_memory_raw.get("transitions", [])
    if not isinstance(transitions_raw, list):
        transitions_raw = []
    world_state_raw = raw.get("world_state", {})
    if not isinstance(world_state_raw, dict):
        world_state_raw = {}
    return {
        "version": int(raw.get("version", 1) or 1),
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
        "shared_context": str(raw.get("shared_context", environment.get("scenario", "")))[:4000],
        "episode_summary": "",
        "public_updates": [],
        "active_proposals": {},
        "variables": variables,
        "world_state": {
            "time_context": str(environment.get("time_context", "")),
            "spatial_context": str(environment.get("spatial_context", "")),
            "current_time": str(world_state_raw.get("current_time", environment.get("time_context", ""))),
            "current_location": str(world_state_raw.get("current_location", environment.get("spatial_context", ""))),
            "temporal_scope": str(world_state_raw.get("temporal_scope", environment.get("time_context", ""))),
            "spatial_scope": str(world_state_raw.get("spatial_scope", environment.get("spatial_context", ""))),
            "conditions": world_state_raw.get("conditions", []) if isinstance(world_state_raw.get("conditions", []), list) else [],
            "active_events": world_state_raw.get("active_events", []) if isinstance(world_state_raw.get("active_events", []), list) else [],
        },
        "timeline": [],
        "state_memory": {
            "version": 1,
            "current_states": {
                "task_state": str(current_states_raw.get("task_state", "planning")),
                "collaboration_state": str(current_states_raw.get("collaboration_state", "aligned")),
                "relationship_state": str(current_states_raw.get("relationship_state", "neutral")),
            },
            "transitions": [t for t in transitions_raw if isinstance(t, dict)][-120:],
        },
    }

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
    phases = state.get("phases", [])
    if not isinstance(phases, list):
        phases = []
    variables = state.get("variables", {})
    if not isinstance(variables, dict):
        variables = {}
    progress = safe_float(variables.get("progress", 0.0))
    confidence = safe_float(variables.get("confidence", 0.5))
    risk = safe_float(variables.get("risk", 0.3))
    alignment = safe_float(variables.get("alignment", 0.5))
    state_memory = state.get("state_memory", {})
    if not isinstance(state_memory, dict):
        state_memory = {}
    current_states = state_memory.get("current_states", {})
    if not isinstance(current_states, dict):
        current_states = {}
    transitions = state_memory.get("transitions", [])
    if not isinstance(transitions, list):
        transitions = []
    world_state = state.get("world_state", {})
    if not isinstance(world_state, dict):
        world_state = {}

    interactions = runtime_input.get("_interactions", [])
    interaction_intensity = 0.0
    if isinstance(interactions, list) and interactions:
        vals: list[float] = []
        for item in interactions:
            if isinstance(item, dict):
                vals.append(safe_float(item.get("intensity", 1.0), 1.0))
        if vals:
            interaction_intensity = sum(vals) / len(vals)

    if status == "succeeded":
        progress += 0.12
        confidence += 0.07
        risk -= 0.03
        alignment += 0.04
    elif status == "failed":
        progress += 0.03
        confidence -= 0.08
        risk += 0.12
        alignment -= 0.06
    elif status == "waiting_human":
        progress += 0.02
        confidence -= 0.03
        risk += 0.04
    else:
        progress += 0.01

    if interaction_intensity > 2.0:
        risk += 0.02
        alignment -= 0.01

    if isinstance(output, dict):
        sim_updates = output.get("simulation_updates", {})
        if isinstance(sim_updates, dict):
            vars_patch = sim_updates.get("variables", {})
            if isinstance(vars_patch, dict):
                progress = safe_float(vars_patch.get("progress", progress), progress)
                confidence = safe_float(vars_patch.get("confidence", confidence), confidence)
                risk = safe_float(vars_patch.get("risk", risk), risk)
                alignment = safe_float(vars_patch.get("alignment", alignment), alignment)
            world_patch = sim_updates.get("world_state", {})
            if isinstance(world_patch, dict):
                world_state = merge_world_state(world_state, world_patch)
            rendered_output = output_text(output)
            if rendered_output:
                active_events = world_state.get("active_events", [])
                if not isinstance(active_events, list):
                    active_events = []
                active_events.append(f"seq#{seq} {node.id}: {rendered_output[:220]}")
                world_state["active_events"] = active_events[-30:]
    state["state_memory"] = derive_state_memory(
        current_states=current_states,
        transitions=transitions,
        runtime_input=runtime_input,
        output=output,
        status=status,
        seq=seq,
    )

    progress = max(0.0, min(1.0, progress))
    confidence = max(0.0, min(1.0, confidence))
    risk = max(0.0, min(1.0, risk))
    alignment = max(0.0, min(1.0, alignment))

    phase_count = max(1, len(phases))
    current_phase_index = min(phase_count - 1, int(progress * phase_count))
    state["current_phase_index"] = current_phase_index
    state["variables"] = {
        "progress": round(progress, 4),
        "confidence": round(confidence, 4),
        "risk": round(risk, 4),
        "alignment": round(alignment, 4),
    }
    state["world_state"] = normalize_world_state(world_state)
    timeline = state.get("timeline", [])
    if not isinstance(timeline, list):
        timeline = []
    phase_name = ""
    if phases and isinstance(phases[current_phase_index], dict):
        phase_name = str(phases[current_phase_index].get("name", ""))
    timeline.append(
        {
            "seq": seq,
            "node_id": node.id,
            "node_type": node.type,
            "status": status,
            "phase_index": current_phase_index,
            "phase_name": phase_name,
            "variables": state["variables"],
            "world_state": state["world_state"],
            "state_memory": state["state_memory"],
        }
    )
    state["timeline"] = timeline[-200:]
    return state

def normalize_world_state(world_state: dict[str, Any]) -> dict[str, Any]:
    conditions = world_state.get("conditions", [])
    active_events = world_state.get("active_events", [])
    return {
        "time_context": str(world_state.get("time_context", "")),
        "spatial_context": str(world_state.get("spatial_context", "")),
        "current_time": str(world_state.get("current_time", "")),
        "current_location": str(world_state.get("current_location", "")),
        "temporal_scope": str(world_state.get("temporal_scope", "")),
        "spatial_scope": str(world_state.get("spatial_scope", "")),
        "conditions": [str(v) for v in conditions if str(v).strip()][-30:] if isinstance(conditions, list) else [],
        "active_events": [str(v) for v in active_events if str(v).strip()][-30:] if isinstance(active_events, list) else [],
    }

def update_distributed_state(
    state: dict[str, Any],
    *,
    node: WorkflowNode,
    output: dict[str, Any],
    seq: int,
) -> dict[str, Any]:
    next_state = dict(state)
    public_updates = list(next_state.get("public_updates", [])) if isinstance(next_state.get("public_updates", []), list) else []
    public_update = str(output.get("shared_effect_claim") or output.get("public_state_update", "")).strip()
    if public_update:
        public_updates.append({"seq": seq, "node_id": node.id, "text": public_update[:1600], "status": "claimed"})
    next_state["public_updates"] = public_updates[-24:]

    proposals = dict(next_state.get("active_proposals", {})) if isinstance(next_state.get("active_proposals", {}), dict) else {}
    proposal = str(output.get("intent") or output.get("proposal_summary", "")).strip()
    if proposal:
        proposals[node.id] = proposal[:1200]
    next_state["active_proposals"] = proposals

    timeline = list(next_state.get("timeline", [])) if isinstance(next_state.get("timeline", []), list) else []
    timeline.append(
        {
            "seq": seq,
            "node_id": node.id,
            "status": "succeeded",
            "episode_index": int(next_state.get("episode_index", 0) or 0),
        }
    )
    next_state["timeline"] = timeline[-200:]
    return next_state

def merge_world_state(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key in ["time_context", "spatial_context", "current_time", "current_location", "temporal_scope", "spatial_scope"]:
        value = patch.get(key)
        if value not in {"", None}:
            merged[key] = str(value)
    for key in ["conditions", "active_events"]:
        value = patch.get(key)
        if isinstance(value, list):
            existing = merged.get(key, [])
            if not isinstance(existing, list):
                existing = []
            merged[key] = [*existing, *[str(v) for v in value if str(v).strip()]][-30:]
    return normalize_world_state(merged)

def derive_state_memory(
    *,
    current_states: dict[str, Any],
    transitions: list[Any],
    runtime_input: dict[str, Any],
    output: dict[str, Any] | None,
    status: str,
    seq: int,
) -> dict[str, Any]:
    states = {
        "task_state": str(current_states.get("task_state", "planning")),
        "collaboration_state": str(current_states.get("collaboration_state", "aligned")),
        "relationship_state": str(current_states.get("relationship_state", "neutral")),
    }
    next_transitions = [t for t in transitions if isinstance(t, dict)]
    text_parts: list[str] = []
    for v in [
        runtime_input.get("prompt"),
        runtime_input.get("question"),
        runtime_input.get("situation"),
        (output or {}).get("content") if isinstance(output, dict) else None,
        (output or {}).get("text") if isinstance(output, dict) else None,
        (output or {}).get("summary") if isinstance(output, dict) else None,
    ]:
        if isinstance(v, str):
            text_parts.append(v.lower())
    all_text = " ".join(text_parts)

    def update_state(key: str, value: str, reason: str) -> None:
        old = str(states.get(key, ""))
        if old == value:
            return
        states[key] = value
        next_transitions.append({"seq": seq, "key": key, "from": old, "to": value, "reason": reason})

    if status == "failed":
        update_state("task_state", "blocked", "node_failed")
        update_state("collaboration_state", "misaligned", "node_failed")
    elif status == "waiting_human":
        update_state("task_state", "awaiting_decision", "waiting_human")
    elif status == "succeeded":
        if any(k in all_text for k in ["final", "summary", "conclusion", "结论", "总结", "交付"]):
            update_state("task_state", "synthesizing", "content_signal")
        elif any(k in all_text for k in ["implement", "execute", "执行", "实现", "编码"]):
            update_state("task_state", "executing", "content_signal")
        elif any(k in all_text for k in ["plan", "scope", "规划", "范围", "假设"]):
            update_state("task_state", "planning", "content_signal")

        if any(k in all_text for k in ["disagree", "conflict", "reject", "反对", "冲突", "否决"]):
            update_state("collaboration_state", "contested", "content_signal")
        elif any(k in all_text for k in ["agree", "align", "consensus", "同意", "一致", "共识"]):
            update_state("collaboration_state", "aligned", "content_signal")

        if any(k in all_text for k in ["leader", "manager", "instruction", "领导", "指示"]):
            update_state("relationship_state", "hierarchical", "content_signal")
        elif any(k in all_text for k in ["peer", "collaborate", "同级", "协作"]):
            update_state("relationship_state", "peer", "content_signal")

    return {"version": 1, "current_states": states, "transitions": next_transitions[-120:]}
