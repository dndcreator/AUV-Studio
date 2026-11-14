"""Entity registry inside Dynamic State, independent of executable workflow nodes."""
from __future__ import annotations

import copy
from typing import Any


def initial_entities(workflow) -> dict[str, Any]:
    config = workflow.environment.simulation.get("dynamic_state", {})
    configured = config.get("entities", {}) if isinstance(config, dict) else {}
    entities = {}
    if isinstance(configured, dict):
        for entity_id, value in configured.items():
            if isinstance(entity_id, str) and entity_id.strip() and isinstance(value, dict):
                if value.get("visibility", "global") not in {"global", "private"} or (
                    value.get("visibility") == "private" and not value.get("owner_id")
                ):
                    raise ValueError("Invalid entity visibility/owner")
                entities[entity_id] = {"id": entity_id, "name": str(value.get("name", entity_id)),
                    "kind": str(value.get("kind", "entity")), "agent_node_ids": [],
                    "visibility": value.get("visibility", "global"), "owner_id": value.get("owner_id", ""),
                    "status": "active", "source": {"kind": "initial_definition", "workflow_version": workflow.version}}
    for node in workflow.nodes:
        if node.type not in {"agent", "external_agent"} or node.id.startswith(("summary_", "report_", "director_")):
            continue
        entity_id = str(node.config.get("entity_id") or node.id)
        if entity_id not in entities:
            entities[entity_id] = {"id": entity_id, "name": str(node.config.get("entity_name") or entity_id),
                "kind": str(node.config.get("entity_type") or "individual"), "agent_node_ids": [],
                "visibility": "global", "owner_id": "", "status": "active",
                "source": {"kind": "initial_definition", "workflow_version": workflow.version}}
        entities[entity_id]["agent_node_ids"].append(node.id)
    return entities


def visible_entities(raw: Any, *, node_id: str = "", include_private: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: copy.deepcopy(value) for key, value in raw.items() if isinstance(value, dict)
            and value.get("status") == "active" and (value.get("visibility") != "private"
            or include_private or bool(node_id) and value.get("owner_id") == node_id)}


def register_entities(entities, operations, validate_refs, *, source_outcome_seq=None):
    rejected, accepted = [], []
    for operation in operations:
        if not isinstance(operation, dict):
            rejected.append({"kind": "entity", "reason": "not_object"})
            continue
        entity_id, kind = operation.get("id"), operation.get("kind")
        refs = validate_refs(operation.get("source_refs"))
        if (operation.get("op") != "register" or not isinstance(entity_id, str) or not entity_id.strip()
            or len(entity_id) > 120 or not isinstance(kind, str) or not kind.strip() or len(kind) > 80 or not refs):
            rejected.append({"kind": "entity", "reason": "invalid_entity_or_source"})
            continue
        visibility, owner = operation.get("visibility", "global"), operation.get("owner_id", "")
        if (visibility not in {"global", "private"} or not isinstance(owner, str)
            or visibility == "private" and (not owner or source_outcome_seq is None and owner not in {ref["node_id"] for ref in refs})
            or operation.get("agent_node_ids")):
            rejected.append({"kind": "entity", "reason": "invalid_entity_authority"})
            continue
        if entity_id in entities:
            existing = entities[entity_id]
            if (existing["kind"], existing["visibility"], existing["owner_id"]) != (kind, visibility, owner):
                rejected.append({"kind": "entity", "reason": "immutable_entity_identity"})
            continue
        entities[entity_id] = {"id": entity_id, "name": str(operation.get("name") or entity_id)[:200],
            "kind": kind, "visibility": visibility, "owner_id": owner, "agent_node_ids": [], "status": "active",
            "source": {"kind": "accepted_event", "source_refs": refs}}
        accepted.append({"op": "register", "id": entity_id, "source_refs": refs})
    return accepted, rejected


def entity_summary(dynamic: dict[str, Any], *, node_id: str = "", include_private: bool = False) -> dict[str, Any]:
    entities = visible_entities(dynamic.get("entities", {}), node_id=node_id, include_private=include_private)
    by_kind: dict[str, int] = {}
    for entity in entities.values():
        kind = entity["kind"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {"entities": entities, "count": len(entities), "by_kind": by_kind}
