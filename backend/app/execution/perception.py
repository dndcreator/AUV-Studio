from __future__ import annotations

from typing import Any

from ..schemas import WorkflowEdge, WorkflowNode
from .graph import build_interaction_message, should_follow_condition


PUBLIC_FIELDS = ("round", "action_index", "event_seq", "node_id", "actor", "participation", "action")
PRIVATE_FIELDS = ("intent", "observation", "self_update", "turn_delta", "shared_effect_claim")


def participant_roster(nodes: list[WorkflowNode], node_id: str, edges: list[WorkflowEdge]) -> list[dict[str, str]]:
    visible = {node_id} | {edge.source for edge in edges if edge.target == node_id} | {
        edge.target for edge in edges if edge.source == node_id}
    return [{"node_id": node.id, "name": str(node.config.get("entity_name") or node.id),
             **({"role": str(node.config.get("role", "custom"))} if node.id == node_id else {})}
            for node in nodes if not edges or node.id in visible]


def delivery_receipts(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = {}
    for action in actions:
        node_id = str(action.get("node_id", ""))
        if node_id:
            receipts = action.get("received_event_seqs")
            latest[node_id] = {"node_id": node_id, "action_index": action.get("action_index"),
                               "received_event_seqs": list(receipts) if isinstance(receipts, list) else None}
    return list(latest.values())


def public_action(action: dict[str, Any]) -> dict[str, Any]:
    """Explicit projection: future internal fields must not become peer-visible by default."""
    result = {key: action[key] for key in PUBLIC_FIELDS if key in action}
    if isinstance(action.get("result"), bool):
        result["result"] = action["result"]
    return result


def actions_for_node(actions: list[dict[str, Any]], node_id: str, edges: list[WorkflowEdge]) -> list[dict[str, Any]]:
    visible = []
    for action in actions:
        source = str(action.get("node_id", ""))
        own = source == node_id
        public = public_action(action)
        if own:
            visible.append({**public, **{key: action[key] for key in PRIVATE_FIELDS if key in action}})
        elif not edges or any(edge.source == source and edge.target == node_id and should_follow_condition(edge, public) for edge in edges):
            visible.append(public)
    return visible


def incoming_interactions(actions: list[dict[str, Any]], node_id: str, edges: list[WorkflowEdge]) -> list[dict[str, Any]]:
    messages = []
    for action in actions[-12:]:
        public = public_action(action)
        for edge in edges:
            if edge.source == action.get("node_id") and edge.target == node_id and should_follow_condition(edge, public):
                message = build_interaction_message(edge, {**public, "content": public.get("action", "")})
                messages.append({**message, "edge_id": edge.id, "event_seq": action.get("event_seq")})
    return messages


def participant_context(
    context: dict[str, Any], *, state: dict[str, Any], memories: list[dict[str, Any]],
    background: dict[str, Any], actions: list[dict[str, Any]], guidance: str,
) -> dict[str, Any]:
    result = {"input": context.get("input", {}), "_run": context.get("_run", {}),
              "_environment": context.get("_runtime_environment", {}),
              "_runtime_environment": context.get("_runtime_environment", {}),
              "_simulation": state, "_memory": memories, "_context_pack": background, "_global_guidance": guidance}
    for action in actions:
        result[str(action.get("node_id", ""))] = {**action, "content": action.get("action", "")}
    return result
