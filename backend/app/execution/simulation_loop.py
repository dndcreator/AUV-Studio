from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..schemas import WorkflowDefinition, WorkflowEdge, WorkflowNode
from .dynamic_state import compact_dynamic_state
from .entity_contracts import entity_action_contract
from .scene_control import scene_setup_instruction
from .perception import delivery_receipts, public_action


@dataclass(frozen=True)
class SimulationLoopPolicy:
    enabled: bool
    max_actions: int
    max_rounds: int
    director_interval_rounds: int


def build_loop_policy(workflow: WorkflowDefinition) -> SimulationLoopPolicy:
    simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
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
    max_actions = max(1, min(max_actions, 120))
    return SimulationLoopPolicy(
        enabled=execution_model == "continuous",
        max_actions=max_actions,
        max_rounds=max_rounds,
        director_interval_rounds=max(1, min(int(simulation.get("director_interval_rounds", 1) or 1), 8)),
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
        "role": str(node.config.get("role", "custom")),
        "identity": str(node.config.get("entity_name") or node.config.get("profile") or node.id),
        "entity_type": entity_type,
        "profile": str(node.config.get("entity_profile") or node.config.get("profile") or ""),
        "responsibilities": node.config.get("responsibilities", []),
        "identity_contract": identity_contract,
        "behavior_rule": str(node.config.get("behavior_prompt") or ""),
        "entity_action_contract": entity_action_contract(entity_type),
        "character_history": character_memory[-3:],
    }


def build_node_descriptors(nodes: list[WorkflowNode]) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node.id,
            "role": str(node.config.get("role", "custom")),
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
        "world_state": state.get("world_state", {}),
        "dynamic_state": compact_dynamic_state(state.get("dynamic_state", {})),
        "recent_actions": [public_action(action) for action in recent_actions[-8:]],
    }


def activation_instruction(
    *,
    state: dict[str, Any],
    recent_actions: list[dict[str, Any]],
    nodes: list[WorkflowNode],
    scene_control: dict[str, Any] | None = None,
    connections: list[WorkflowEdge] | None = None,
) -> str:
    scene = scene_control or {}
    return (
        "Select only the nodes that are meaningfully involved in the next step of this distributed process. "
        "Use semantic judgment from the shared state and node definitions. Do not activate every node for fairness, "
        "and do not invent what any node will decide. Consider the directed connections when judging reachability; "
        "activation itself does not deliver messages or make all active nodes share knowledge. Do not expose private "
        "profile details in shared scene contracts or routing explanations. This applies equally to simulation, research, collaboration, "
        "and roleplay. Return JSON only: "
        '{"active_node_ids":["node_id"],"reason":"brief routing reason"}.\n\n'
        + (scene_setup_instruction() if not scene.get("contract") else "Preserve the established Scene Contract. ")
        + f"\nSCENE CONTRACT:\n{json.dumps(scene.get('contract', {}), ensure_ascii=False)}\n\n"
        + f"SHARED STATE:\n{json.dumps(build_shared_state_view(state, recent_actions), ensure_ascii=False, default=str)}\n\n"
        + "CONNECTIONS (directed delivery; no entity edges means a shared public scene; never grant private knowledge):\n"
        + json.dumps([edge.model_dump() for edge in connections or []], ensure_ascii=False) + "\n\n"
        + "ACTUAL INPUT RECEIPTS (source event IDs supplied before each node's latest completed action; "
        "null means not recorded, not zero deliveries; a possible channel does not imply delivery):\n"
        + json.dumps(delivery_receipts(recent_actions), ensure_ascii=False) + "\n\n"
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
    return {"active_node_ids": active_ids, "reason": str(value.get("reason", "")).strip(), "scene_contract": value.get("scene_contract")}


def build_context_packet(
    *,
    state: dict[str, Any],
    memories: list[dict[str, Any]],
    recent_actions: list[dict[str, Any]],
    director_guidance: str,
    background_context: dict[str, Any] | None,
    round_index: int,
    action_index: int,
    scene_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "round": round_index,
        "action_index": action_index,
        "scene_contract": scene_contract or {},
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
        "detail. Follow the current Scene Contract for pacing, detail, and progress_scale independently. "
        "Detail means selectively showing meaningful particulars, not a word quota or checklist: dialogue may include "
        "your exact words and behavior; analytical work may include your reasoning, evidence, uncertainty, and result. "
        "Use only the detail needed for this contribution. Do not repeat established information or add atmosphere, "
        "gestures, explanations, or fabricated developments to fill space. Small relevant changes are enough at micro "
        "scale; waiting or silence can be appropriate. Report turn_delta as the grounded change since the preceding "
        "interaction; it may be empty. Never invent novelty to satisfy this field. "
        "Meaningful initiative should originate from participants rather than the director. Follow the entity action contract "
        "in ROLE before any optional custom behavior rule. Produce an in-world action or response, never a paraphrase of this "
        "instruction or the requested task. Keep one turn focused on one "
        "concrete contribution, and prioritize syntactically complete JSON over extra length. Return JSON only with: "
        "participation (act|observe|wait), action (only your externally observable words, behavior or published work; "
        "put unspoken plans and inner thoughts in private fields, not action), intent (your private immediate aim), "
        "turn_delta (brief change claim, or empty if nothing changed), "
        "observation (what you personally observed or inferred, with uncertainty), self_update (your own continuing state), "
        "shared_effect_claim (a claim about effects on the shared situation, not automatically accepted as fact), and "
        "episode_signal (continue|ready). Incoming interactions describe delivered messages, not compulsory decisions. "
        "A connection never grants access to another participant's unspoken intentions or private observations.\n\n"
        "In directed communication mode, send only through your listed outgoing channels. With no outgoing channel, "
        "you may plan or propose a message but cannot claim it was delivered or that a recipient received it. "
        "Incoming messages do not create a reverse channel.\n\n"
        "Your explicit ROLE.role is your own responsibility, not an absent participant to hand work to. "
        "Do not silently invent extra participants to perform an existing role. Unmodeled contacts can be proposed, "
        "not claimed as completed communications.\n\n"
        f"CONTEXT:\n{json.dumps(context_packet, ensure_ascii=False, default=str)}\n\n"
        f"ROLE:\n{json.dumps(role_packet, ensure_ascii=False, default=str)}"
    )


def normalize_action_output(output: dict[str, Any], *, round_index: int, action_index: int) -> dict[str, Any]:
    content = str(output.get("content") or output.get("text") or output.get("action") or "").strip()
    parsed: dict[str, Any] = output if isinstance(output.get("action"), str) else {}
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
    action = str(parsed["action"]).strip() if isinstance(parsed.get("action"), str) else content
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
        "turn_delta": str(parsed.get("turn_delta", "")).strip(),
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
        **({"result": parsed["result"]} if isinstance(parsed.get("result"), bool) else {}),
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
        "state_patch": value.get("state_patch", {}) if isinstance(value.get("state_patch", {}), dict) else {},
        "round_progress": value.get("round_progress", []) if isinstance(value.get("round_progress"), list) else [],
        "scene_contract": value.get("scene_contract") if isinstance(value.get("scene_contract"), dict) else None,
    }
