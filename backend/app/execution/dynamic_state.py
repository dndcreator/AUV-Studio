from __future__ import annotations

import copy
import json
import re
from typing import Any


_CONCEPT_ID_RE = re.compile(r"[^a-z0-9_]+")


def init_dynamic_state(config: Any) -> dict[str, Any]:
    raw = config if isinstance(config, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "version": 1,
        "schema_version": 0,
        "concepts": {},
        "values": {},
        "history": [],
        "limits": {
            "max_concepts": _bounded_int(raw.get("max_concepts"), 48, 8, 120),
            "max_per_owner": _bounded_int(raw.get("max_per_owner"), 10, 2, 30),
            "max_new_per_round": _bounded_int(raw.get("max_new_per_round"), 3, 1, 10),
            "max_history": _bounded_int(raw.get("max_history"), 160, 20, 500),
        },
    }


def dynamic_state_enabled(state: dict[str, Any]) -> bool:
    dynamic = state.get("dynamic_state", {}) if isinstance(state, dict) else {}
    return isinstance(dynamic, dict) and bool(dynamic.get("enabled", False))


def state_coder_audit_clause(actions: list[dict[str, Any]]) -> str:
    return (
        "As a separate State Coder pass in this same response, inspect UNCODED ACTIONS and propose only the smallest "
        "persistent state needed to constrain future behavior. Do not use predefined domain concepts. Create a concept only "
        "when forgetting it could change a future action or conclusion, it persists beyond one action, and no existing "
        "concept represents it. Do not store prose, style, ordinary dialogue, transient gestures, or duplicate facts. Reuse "
        "existing concepts. Private knowledge requires visibility=private and owner_id set to the exact source node_id. "
        "Put the proposal in state_patch with "
        "schema_ops and state_ops. Upserts require id, description, scope, owner_id, value_type, visibility, retention, "
        "future_relevance, and reason. State sets require concept_id, value, confidence, reason, and source_node_ids. Use "
        "empty arrays when nothing deserves persistent state.\n\n"
        f"UNCODED ACTIONS:\n{json.dumps(actions, ensure_ascii=False, default=str)}\n\n"
    )


def state_coder_instruction(*, state: dict[str, Any], actions: list[dict[str, Any]], round_index: int) -> str:
    dynamic = compact_dynamic_state(state.get("dynamic_state", {}), include_private=True)
    context = {
        "mode": state.get("mode", ""),
        "task_contract": state.get("task_contract", {}),
        "current_phase_index": state.get("current_phase_index", 0),
        "episode_index": state.get("episode_index", 0),
        "world_state": state.get("world_state", {}),
        "existing_dynamic_state": dynamic,
        "new_actions": actions,
    }
    return (
        "You are the State Coder for an open-ended distributed simulation. Maintain only the smallest authoritative set "
        "of concepts whose current values can change a future participant's valid action, decision, interpretation, or "
        "deliverable. There is no domain schema: discover concepts from the actual process. Do not store prose, style, "
        "ordinary dialogue, transient gestures, duplicate facts, or details useful only for retelling the current action. "
        "Before creating a concept, apply three gates: forgetting it could change a future outcome; it persists beyond this "
        "single action; and no existing concept can represent it. Reuse existing concepts whenever possible. Private knowledge "
        "must use visibility=private and owner_id set to the exact source node_id. A state claim is not automatically true; "
        "use confidence and preserve "
        "uncertainty. Retire a concept only when it is resolved and cannot constrain future behavior. Return JSON only with "
        "schema_ops and state_ops. schema_ops support upsert or retire. state_ops support set or unset. Each upsert requires "
        "id, description, scope(global|entity), owner_id, value_type(scalar|text|list|object), visibility(global|private), "
        "retention, future_relevance, and reason. Each state operation requires concept_id, value for set, confidence, reason, "
        "and source_node_ids. Emit empty arrays when nothing deserves persistent state.\n\n"
        f"ROUND: {round_index}\n"
        f"CONTEXT:\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
        "OUTPUT SHAPE:\n"
        '{"schema_ops":[{"op":"upsert|retire","id":"concept_id","description":"",'
        '"scope":"global|entity","owner_id":"","value_type":"scalar|text|list|object",'
        '"visibility":"global|private","retention":"","future_relevance":"","reason":""}],'
        '"state_ops":[{"op":"set|unset","concept_id":"concept_id","value":null,'
        '"confidence":0.0,"reason":"","source_node_ids":[""]}]}'
    )


def parse_state_coder_output(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```json"):
        candidate = candidate[7:]
    elif candidate.startswith("```"):
        candidate = candidate[3:]
    if candidate.endswith("```"):
        candidate = candidate[:-3]
    candidate = candidate.strip()
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return {"schema_ops": [], "state_ops": [], "parse_error": "invalid_json"}
    if not isinstance(parsed, dict):
        return {"schema_ops": [], "state_ops": [], "parse_error": "not_object"}
    return {
        "schema_ops": parsed.get("schema_ops", []) if isinstance(parsed.get("schema_ops", []), list) else [],
        "state_ops": parsed.get("state_ops", []) if isinstance(parsed.get("state_ops", []), list) else [],
    }


def apply_dynamic_state_proposal(
    current: dict[str, Any],
    proposal: dict[str, Any],
    *,
    seq: int,
    source_node_ids: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _normalize_dynamic_state(current)
    concepts = state["concepts"]
    values = state["values"]
    limits = state["limits"]
    accepted_schema: list[dict[str, Any]] = []
    accepted_state: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    new_count = 0

    for raw in proposal.get("schema_ops", []):
        if not isinstance(raw, dict):
            rejected.append({"kind": "schema", "reason": "not_object"})
            continue
        operation = str(raw.get("op", "upsert")).strip().lower()
        concept_id = _concept_id(raw.get("id") or raw.get("concept_id"))
        if not concept_id:
            rejected.append({"kind": "schema", "reason": "invalid_id"})
            continue
        if operation == "retire":
            existing = concepts.get(concept_id)
            if not isinstance(existing, dict) or existing.get("status") == "retired":
                rejected.append({"kind": "schema", "id": concept_id, "reason": "unknown_concept"})
                continue
            existing["status"] = "retired"
            existing["updated_seq"] = seq
            existing["retired_reason"] = _short_text(raw.get("reason"), 320)
            values.pop(concept_id, None)
            accepted_schema.append({"op": "retire", "id": concept_id})
            continue
        if operation != "upsert":
            rejected.append({"kind": "schema", "id": concept_id, "reason": "unsupported_operation"})
            continue

        description = _short_text(raw.get("description"), 480)
        future_relevance = _short_text(raw.get("future_relevance"), 480)
        reason = _short_text(raw.get("reason"), 320)
        if not description or not future_relevance or not reason:
            rejected.append({"kind": "schema", "id": concept_id, "reason": "missing_relevance_evidence"})
            continue
        is_new = concept_id not in concepts or concepts.get(concept_id, {}).get("status") == "retired"
        if is_new and new_count >= limits["max_new_per_round"]:
            rejected.append({"kind": "schema", "id": concept_id, "reason": "round_budget"})
            continue
        active_count = sum(1 for item in concepts.values() if isinstance(item, dict) and item.get("status") == "active")
        if is_new and active_count >= limits["max_concepts"]:
            rejected.append({"kind": "schema", "id": concept_id, "reason": "concept_budget"})
            continue
        scope = str(raw.get("scope", "global")).strip().lower()
        if scope not in {"global", "entity"}:
            scope = "global"
        owner_id = _short_text(raw.get("owner_id"), 120) if scope == "entity" else ""
        if scope == "entity" and not owner_id:
            rejected.append({"kind": "schema", "id": concept_id, "reason": "missing_owner"})
            continue
        if is_new and owner_id:
            owner_count = sum(
                1
                for item in concepts.values()
                if isinstance(item, dict) and item.get("status") == "active" and item.get("owner_id") == owner_id
            )
            if owner_count >= limits["max_per_owner"]:
                rejected.append({"kind": "schema", "id": concept_id, "reason": "owner_budget"})
                continue
        visibility = str(raw.get("visibility", "global")).strip().lower()
        if visibility not in {"global", "private"}:
            visibility = "global"
        if visibility == "private" and not owner_id:
            rejected.append({"kind": "schema", "id": concept_id, "reason": "private_without_owner"})
            continue
        value_type = str(raw.get("value_type", "text")).strip().lower()
        if value_type not in {"scalar", "text", "list", "object"}:
            value_type = "text"
        prior = concepts.get(concept_id, {}) if isinstance(concepts.get(concept_id), dict) else {}
        concepts[concept_id] = {
            "id": concept_id,
            "description": description,
            "scope": scope,
            "owner_id": owner_id,
            "value_type": value_type,
            "visibility": visibility,
            "retention": _short_text(raw.get("retention"), 320),
            "future_relevance": future_relevance,
            "status": "active",
            "created_seq": int(prior.get("created_seq", seq) or seq),
            "updated_seq": seq,
        }
        if is_new:
            new_count += 1
        accepted_schema.append({"op": "upsert", "id": concept_id, "new": is_new})

    for raw in proposal.get("state_ops", []):
        if not isinstance(raw, dict):
            rejected.append({"kind": "state", "reason": "not_object"})
            continue
        operation = str(raw.get("op", "set")).strip().lower()
        concept_id = _concept_id(raw.get("concept_id") or raw.get("id"))
        concept = concepts.get(concept_id)
        if not concept_id or not isinstance(concept, dict) or concept.get("status") != "active":
            rejected.append({"kind": "state", "id": concept_id, "reason": "unknown_concept"})
            continue
        if operation == "unset":
            values.pop(concept_id, None)
            accepted_state.append({"op": "unset", "concept_id": concept_id})
            continue
        if operation != "set":
            rejected.append({"kind": "state", "id": concept_id, "reason": "unsupported_operation"})
            continue
        reason = _short_text(raw.get("reason"), 320)
        if not reason:
            rejected.append({"kind": "state", "id": concept_id, "reason": "missing_reason"})
            continue
        supplied_sources = raw.get("source_node_ids", [])
        sources = [str(item)[:120] for item in supplied_sources if str(item).strip()] if isinstance(supplied_sources, list) else []
        sources = list(dict.fromkeys([*sources, *source_node_ids]))[:12]
        values[concept_id] = {
            "value": _bounded_value(raw.get("value")),
            "confidence": round(max(0.0, min(1.0, _safe_float(raw.get("confidence"), 0.6))), 4),
            "reason": reason,
            "updated_seq": seq,
            "source_node_ids": sources,
        }
        accepted_state.append({"op": "set", "concept_id": concept_id})

    changed = bool(accepted_schema or accepted_state)
    if changed:
        state["version"] = int(state.get("version", 1) or 1) + 1
        if accepted_schema:
            state["schema_version"] = int(state.get("schema_version", 0) or 0) + 1
        history = state["history"]
        history.append(
            {
                "seq": seq,
                "schema_ops": accepted_schema,
                "state_ops": accepted_state,
                "source_node_ids": list(dict.fromkeys(source_node_ids))[:12],
            }
        )
        state["history"] = history[-limits["max_history"] :]
    result = {
        "changed": changed,
        "version": state["version"],
        "schema_version": state["schema_version"],
        "accepted_schema_ops": accepted_schema,
        "accepted_state_ops": accepted_state,
        "rejected": rejected,
        "active_concepts": sum(1 for item in concepts.values() if isinstance(item, dict) and item.get("status") == "active"),
    }
    return state, result


def simulation_state_for_node(state: dict[str, Any], node_id: str) -> dict[str, Any]:
    view = copy.deepcopy(state)
    view["dynamic_state"] = compact_dynamic_state(state.get("dynamic_state", {}), node_id=node_id)
    return view


def compact_dynamic_state(raw: Any, *, node_id: str = "", include_private: bool = False) -> dict[str, Any]:
    state = _normalize_dynamic_state(raw)
    visible_concepts: dict[str, Any] = {}
    visible_values: dict[str, Any] = {}
    for concept_id, concept in state["concepts"].items():
        if not isinstance(concept, dict) or concept.get("status") != "active":
            continue
        private = concept.get("visibility") == "private"
        if private and not include_private and concept.get("owner_id") != node_id:
            continue
        visible_concepts[concept_id] = copy.deepcopy(concept)
        if concept_id in state["values"]:
            visible_values[concept_id] = copy.deepcopy(state["values"][concept_id])
    return {
        "enabled": state["enabled"],
        "version": state["version"],
        "schema_version": state["schema_version"],
        "concepts": visible_concepts,
        "values": visible_values,
        "limits": copy.deepcopy(state["limits"]),
    }


def _normalize_dynamic_state(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    baseline = init_dynamic_state(source.get("limits", source))
    baseline["enabled"] = bool(source.get("enabled", baseline["enabled"]))
    baseline["version"] = _bounded_int(source.get("version"), 1, 1, 1_000_000)
    baseline["schema_version"] = _bounded_int(source.get("schema_version"), 0, 0, 1_000_000)
    baseline["concepts"] = copy.deepcopy(source.get("concepts", {})) if isinstance(source.get("concepts", {}), dict) else {}
    baseline["values"] = copy.deepcopy(source.get("values", {})) if isinstance(source.get("values", {}), dict) else {}
    baseline["history"] = copy.deepcopy(source.get("history", [])) if isinstance(source.get("history", []), list) else []
    return baseline


def _concept_id(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = _CONCEPT_ID_RE.sub("_", text).strip("_")
    return text[:80]


def _short_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return _short_text(value, 320)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded_value(item, depth=depth + 1) for key, item in list(value.items())[:20]}
    return _short_text(value, 320)
