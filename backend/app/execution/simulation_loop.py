from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..schemas import WorkflowDefinition, WorkflowNode
from .entity_contracts import entity_action_contract


@dataclass(frozen=True)
class SimulationLoopPolicy:
    enabled: bool
    max_actions: int
    max_rounds: int
    director_interval_rounds: int
    min_actions_before_stop: int


def build_loop_policy(workflow: WorkflowDefinition) -> SimulationLoopPolicy:
    simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
    mode = str(simulation.get("mode", "")).strip().lower()
    execution_model = str(simulation.get("execution_model", "")).strip().lower()
    contract = simulation.get("output_contract", {})
    if not isinstance(contract, dict):
        contract = {}
    actors = [node for node in workflow.nodes if is_entity_node(node)]
    actor_count = max(1, len(actors))
    max_rounds = max(1, min(int(simulation.get("horizon_rounds", 4) or 4), 24))
    unit = str(contract.get("unit", "")).strip().lower()
    # Deliverable sections/chapters constrain synthesis, not the number of
    # state-changing turns that produce the simulation behind that deliverable.
    action_units = {"utterance", "action", "turn", "line"}
    requested_actions = int(contract.get("max_items", 0) or 0) if unit in action_units else 0
    max_actions = requested_actions if requested_actions > 0 else max_rounds * actor_count
    max_actions = max(actor_count, min(max_actions, 120))
    return SimulationLoopPolicy(
        enabled=execution_model == "continuous",
        max_actions=max_actions,
        max_rounds=max_rounds,
        director_interval_rounds=max(1, min(int(simulation.get("director_interval_rounds", 1) or 1), 8)),
        min_actions_before_stop=min(max_actions, max(actor_count, int(contract.get("min_items", 0) or 0))),
    )


def is_entity_node(node: WorkflowNode) -> bool:
    if node.type not in {"agent", "external_agent"}:
        return False
    return not node.id.startswith(("summary_", "report_", "director_"))


def build_role_packet(node: WorkflowNode, memories: list[dict[str, Any]]) -> dict[str, Any]:
    character_memory = [
        item
        for item in memories
        if isinstance(item, dict)
        and str(item.get("kind", "")) == "character"
        and (str(item.get("node_id", "")) == node.id or str(item.get("subject", "")) == str(node.config.get("entity_name", "")))
    ]
    identity_contract = node.config.get("identity_contract", {})
    if not isinstance(identity_contract, dict):
        identity_contract = {}
    entity_type = str(node.config.get("entity_type") or node.config.get("role") or "individual")
    return {
        "node_id": node.id,
        "identity": str(node.config.get("entity_name") or node.config.get("profile") or node.id),
        "entity_type": entity_type,
        "profile": str(node.config.get("entity_profile") or node.config.get("profile") or ""),
        "responsibilities": node.config.get("responsibilities", []),
        "identity_contract": identity_contract,
        "behavior_rule": str(node.config.get("behavior_prompt") or ""),
        "entity_action_contract": entity_action_contract(entity_type),
        "current_character_state": character_memory[-1] if character_memory else {},
    }


def build_node_descriptors(nodes: list[WorkflowNode]) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node.id,
            "identity": str(node.config.get("entity_name") or node.config.get("profile") or node.id)[:240],
            "entity_type": str(node.config.get("entity_type") or node.config.get("role") or node.type),
            "profile": str(node.config.get("entity_profile") or node.config.get("profile") or "")[:600],
            "responsibilities": node.config.get("responsibilities", []),
            "identity_contract": node.config.get("identity_contract", {}),
        }
        for node in nodes
    ]


def build_shared_state_view(state: dict[str, Any], recent_actions: list[dict[str, Any]]) -> dict[str, Any]:
    phases = state.get("phases", []) if isinstance(state.get("phases", []), list) else []
    phase_index = int(state.get("current_phase_index", 0) or 0)
    current_phase = phases[phase_index] if 0 <= phase_index < len(phases) and isinstance(phases[phase_index], dict) else {}
    return {
        "mode": state.get("mode", ""),
        "task_contract": state.get("task_contract", {}),
        "current_phase": current_phase,
        "shared_context": str(state.get("shared_context", ""))[-2400:],
        "world_state": state.get("world_state", {}),
        "recent_actions": recent_actions[-8:],
    }


def activation_instruction(
    *,
    state: dict[str, Any],
    recent_actions: list[dict[str, Any]],
    nodes: list[WorkflowNode],
) -> str:
    return (
        "Select only the nodes that are meaningfully involved in the next step of this distributed process. "
        "Use semantic judgment from the shared state and node definitions. Do not activate every node for fairness, "
        "and do not invent what any node will decide. This applies equally to simulation, research, collaboration, "
        "and roleplay. Return JSON only: "
        '{"active_node_ids":["node_id"],"reason":"brief routing reason"}.\n\n'
        f"SHARED STATE:\n{json.dumps(build_shared_state_view(state, recent_actions), ensure_ascii=False, default=str)}\n\n"
        f"AVAILABLE NODES:\n{json.dumps(build_node_descriptors(nodes), ensure_ascii=False, default=str)}"
    )


def parse_activation(content: str, nodes: list[WorkflowNode], fallback_index: int = 0) -> dict[str, Any]:
    candidate = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    known = {node.id for node in nodes}
    requested = value.get("active_node_ids", [])
    active_ids = [str(node_id) for node_id in requested if str(node_id) in known] if isinstance(requested, list) else []
    active_ids = list(dict.fromkeys(active_ids))[: max(1, min(4, len(nodes)))]
    if not active_ids and nodes:
        active_ids = [nodes[fallback_index % len(nodes)].id]
    return {"active_node_ids": active_ids, "reason": str(value.get("reason", "")).strip()}


def build_context_packet(
    *,
    state: dict[str, Any],
    memories: list[dict[str, Any]],
    recent_actions: list[dict[str, Any]],
    director_guidance: str,
    background_context: dict[str, Any] | None,
    round_index: int,
    action_index: int,
) -> dict[str, Any]:
    return {
        "round": round_index,
        "action_index": action_index,
        "state": state,
        "memory": memories,
        "recent_actions": recent_actions[-12:],
        "director_guidance": director_guidance,
        "background": background_context or {"entries": []},
        "evidence": state.get("evidence", {}) if isinstance(state, dict) else {},
    }


def action_instruction(context_packet: dict[str, Any], role_packet: dict[str, Any]) -> str:
    return (
        "You are an autonomous participant taking one turn in a distributed process, not the author of the whole process. "
        "First decide whether your identity, knowledge, goals, and current situation justify participation. Control only "
        "yourself: never write another participant's action, reaction, thoughts, conclusion, or decision, and never turn an "
        "unobserved assumption into a global fact. Follow the shared task contract for language, format, style, and level of "
        "detail. When you act, make the action concrete and as rich as the requested deliverable needs: dialogue may include "
        "your exact words and behavior; analytical work may include your reasoning, evidence, uncertainty, and result. "
        "Meaningful initiative should originate from participants rather than the director. Follow the entity action contract "
        "in ROLE before any optional custom behavior rule. Produce an in-world action or response, never a paraphrase of this "
        "instruction or the requested task. Keep one turn focused on one "
        "concrete contribution, and prioritize syntactically complete JSON over extra length. Return JSON only with: "
        "participation (act|observe|wait), action (readable contribution produced only by you), intent (your immediate aim), "
        "observation (what you personally observed or inferred, with uncertainty), self_update (your own continuing state), "
        "shared_effect_claim (a claim about effects on the shared situation, not automatically accepted as fact), and "
        "episode_signal (continue|ready).\n\n"
        f"CONTEXT:\n{json.dumps(context_packet, ensure_ascii=False, default=str)}\n\n"
        f"ROLE:\n{json.dumps(role_packet, ensure_ascii=False, default=str)}"
    )


def normalize_action_output(output: dict[str, Any], *, round_index: int, action_index: int) -> dict[str, Any]:
    content = str(output.get("content") or output.get("text") or output.get("action") or "").strip()
    parsed: dict[str, Any] = {}
    candidate = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                parsed = value
        except json.JSONDecodeError:
            parsed = {}
    participation = str(parsed.get("participation", "act")).strip().lower()
    if participation not in {"act", "observe", "wait"}:
        participation = "act"
    action = str(parsed.get("action") or content).strip()
    updates = parsed.get("state_updates", output.get("simulation_updates", {}))
    if not isinstance(updates, dict):
        updates = {}
    intent = str(parsed.get("intent") or parsed.get("proposal_summary", "")).strip()
    shared_effect_claim = str(parsed.get("shared_effect_claim") or parsed.get("public_state_update", "")).strip()
    self_update = str(parsed.get("self_update") or parsed.get("private_state_update", "")).strip()
    episode_signal = str(parsed.get("episode_signal") or parsed.get("boundary_signal", "continue")).strip().lower()
    if episode_signal not in {"continue", "ready"}:
        episode_signal = "continue"
    return {
        **output,
        "content": action,
        "action": action,
        "participation": participation,
        "intent": intent,
        "observation": str(parsed.get("observation", "")).strip(),
        "self_update": self_update,
        "shared_effect_claim": shared_effect_claim,
        "episode_signal": episode_signal,
        "simulation_updates": updates,
        # Legacy aliases keep old run records and integrations readable.
        "proposal_summary": intent,
        "public_state_update": shared_effect_claim,
        "private_state_update": self_update,
        "boundary_signal": "ready" if episode_signal == "ready" else "none",
        "round": round_index,
        "action_index": action_index,
    }


def parse_director_control(content: str, *, default_guidance: str = "") -> dict[str, Any]:
    candidate = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    decision = str(value.get("decision", "continue")).strip().lower()
    if decision not in {"continue", "transition", "stop"}:
        decision = "continue"
    return {
        "decision": decision,
        "guidance": str(value.get("guidance") or default_guidance).strip(),
        "reason": str(value.get("reason") or "").strip(),
        "shared_state_summary": str(value.get("shared_state_summary") or "").strip(),
    }
