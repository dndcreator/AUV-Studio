from __future__ import annotations

from typing import Any

from ..schemas import WorkflowDefinition
from .dynamic_state import compact_dynamic_state
from .perception import actions_for_node, incoming_interactions
from .simulation_loop import is_entity_node


class ActionBoundaryError(ValueError):
    pass


def validate_actor_context(workflow: WorkflowDefinition, state: dict[str, Any], actions: list[dict[str, Any]],
                           runtime_input: dict[str, Any]) -> None:
    """Validate engine-owned references and permissions, never interpret natural-language actions."""
    entities = {node.id for node in workflow.nodes if is_entity_node(node)}
    actor = runtime_input.get("role", {}).get("node_id")
    if not isinstance(actor, str) or actor not in entities:
        raise ActionBoundaryError("Participant input references an unknown actor.")
    edges = [edge for edge in workflow.edges if edge.source in entities and edge.target in entities]
    allowed = {row["event_seq"]: row for row in actions_for_node(actions, actor, edges)}
    packet = runtime_input.get("context", {})
    for row in packet.get("recent_actions", []):
        expected = allowed.get(row.get("event_seq"))
        if expected is None or any(key not in expected or expected[key] != value for key, value in row.items()):
            raise ActionBoundaryError("Participant input contains an unavailable or altered event.")
    messages = {(row["event_seq"], row["edge_id"]): row
                for row in incoming_interactions(list(allowed.values()), actor, edges)}
    for message in packet.get("interactions", []):
        if messages.get((message.get("event_seq"), message.get("edge_id"))) != message:
            raise ActionBoundaryError("Participant input uses an unavailable communication channel.")
    view = compact_dynamic_state(state.get("dynamic_state", {}), node_id=actor)
    supplied = packet.get("state", {}).get("dynamic_state", {})
    for field in ("concepts", "values"):
        for key, value in supplied.get(field, {}).items():
            if key not in view[field] or view[field][key] != value:
                raise ActionBoundaryError("Participant input contains unavailable or altered state.")
