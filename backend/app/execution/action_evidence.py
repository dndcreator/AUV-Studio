from __future__ import annotations

import json
from typing import Any

from ..schemas import WorkflowDefinition
from .action_boundary import validate_actor_context
from .context_book import estimate_tokens
from .dynamic_state import compact_dynamic_state
from .perception import PRIVATE_FIELDS, public_action
from .simulation_loop import is_entity_node
from .state_coding import pending_state_actions


def build_consistency_evidence(
    workflow: WorkflowDefinition, state: dict[str, Any], actions: list[dict[str, Any]], runtime_input: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read existing authorities; this projection never becomes a second state store."""
    if runtime_input.get("_participant_turn") is True:
        validate_actor_context(workflow, state, actions, runtime_input)
    evidence: list[dict[str, Any]] = []

    def add(source: str, kind: str, value: Any) -> None:
        if value not in (None, "", [], {}):
            evidence.append({"id": source, "kind": kind, "value": value})

    environment = workflow.environment
    for field in ("profile", "scenario", "time_context", "spatial_context", "glossary"):
        add(f"environment.{field}", "setting", getattr(environment, field))
    for field in ("facts", "constraints"):
        for index, value in enumerate(getattr(environment, field)):
            add(f"environment.{field}.{index}", "fact" if field == "facts" else "rule", value)
    # Activation and relevance heuristics must not hide an established constraint from the checker.
    for entry in environment.context_book.entries:
        if entry.enabled:
            add(f"book.{entry.id}", entry.kind, {"content": entry.content, "visibility": entry.visibility.model_dump()})
    fields = ("entity_name", "entity_type", "role", "entity_profile", "profile", "identity_contract",
              "responsibilities", "behavior_prompt", "system_prompt", "user_prompt")
    for node in workflow.nodes:
        if is_entity_node(node):
            add(f"entity.{node.id}", "definition", {field: node.config[field] for field in fields if field in node.config})
    add("task.contract", "task", state.get("task_contract", {}))
    add("world.coordinates", "coordinates", state.get("world_state", {}))
    dynamic = compact_dynamic_state(state.get("dynamic_state", {}), include_private=True)
    for concept_id, concept in dynamic.get("concepts", {}).items():
        add(f"dynamic.{concept_id}", "state", {"schema": concept, "current": dynamic.get("values", {}).get(concept_id)})
    add("research.evidence", "evidence", state.get("evidence", {}))
    pending = pending_state_actions(state.get("dynamic_state", {}), actions) if dynamic.get("enabled") else []
    add("state.coding", "coding_progress", {"last_coded_event_seq": dynamic.get("last_coded_event_seq", 0),
                                           "pending_event_seqs": [row["event_seq"] for row in pending]})
    selected = {row["event_seq"]: row for row in actions[-12:] + pending}
    for seq in sorted(selected):
        add(f"event.{seq}", "accepted_action", public_action(selected[seq]))
    packet = runtime_input.get("context", {})
    known_events = {row["id"] for row in evidence}
    actor_view = {key: value for key, value in packet.items() if key not in {"state", "recent_actions", "environment"}}
    actor_view["received_actions"] = [
        {**({"event_seq": row["event_seq"]} if f"event.{row.get('event_seq')}" in known_events else row),
         **{key: row[key] for key in PRIVATE_FIELDS if key in row}}
        for row in packet.get("recent_actions", [])]
    actor_state = packet.get("state", {})
    actor_view["visible_dynamic_ids"] = list(actor_state.get("dynamic_state", {}).get("values", {}))
    add("actor.input", "actual_input", {"context": actor_view, "role": runtime_input.get("role", {})})
    return evidence


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def project_evidence(evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Replace only exact repeated values with aliases to earlier rows; retain every source ID."""
    seen: dict[str, tuple[str, list[str | int]]] = {}
    projected = []
    alias_count = 0
    for row in evidence:
        refs: list[dict[str, Any]] = []

        def visit(value: Any, path: list[str | int]) -> Any:
            encoded = _json(value)
            source = seen.get(encoded)
            if source is not None:
                alias = {"path": path, "source": source[0], "source_path": source[1]}
                if len(encoded) > len(_json(alias)) + 40:
                    refs.append(alias)
                    return None
            if isinstance(value, dict):
                return {key: visit(item, [*path, key]) for key, item in value.items()}
            if isinstance(value, list):
                return [visit(item, [*path, index]) for index, item in enumerate(value)]
            return value

        def index(value: Any, path: list[str | int]) -> None:
            encoded = _json(value)
            if len(encoded) > 80:
                seen.setdefault(encoded, (row["id"], path))
            if isinstance(value, dict):
                for key, item in value.items():
                    index(item, [*path, key])
            elif isinstance(value, list):
                for offset, item in enumerate(value):
                    index(item, [*path, offset])

        item = {**row, **({"value": visit(row["value"], [])} if "value" in row else {})}
        if refs:
            item["refs"] = refs
        projected.append(item)
        alias_count += len(refs)
        # Sources are always earlier rows, so aliases are acyclic even for repeated nested data.
        index(row.get("value"), [])
    return projected, alias_count


def _block(row: dict[str, Any]) -> str:
    source = row["id"]
    if source.startswith(("environment.", "book.", "entity.")):
        return "stable_evidence"
    if source.startswith("event."):
        return "accepted_history"
    return "actor_input" if source == "actor.input" else "current_state"


def build_check_prompt(
    evidence: list[dict[str, Any]], candidate: dict[str, Any], system_prompt: str,
) -> tuple[str, dict[str, Any]]:
    projected, aliases = project_evidence(evidence)
    # Dict order is intentional: changing the candidate must not invalidate the evidence prefix.
    prompt = json.dumps({"EVIDENCE": projected, "CANDIDATE": candidate}, ensure_ascii=False, separators=(",", ":"), default=str)
    original = json.dumps({"CANDIDATE": candidate, "EVIDENCE": evidence}, ensure_ascii=False, default=str)
    blocks = {name: "" for name in ("stable_evidence", "current_state", "accepted_history", "actor_input")}
    for row in projected:
        blocks[_block(row)] += _json(row)
    blocks["candidate"] = _json(candidate)
    blocks["envelope"] = " " * (len(prompt) - sum(len(text) for text in blocks.values()))
    blocks["system"] = system_prompt
    metrics = {
        "estimator": "cjk-char-ascii-4", "evidence_count": len(evidence), "aliases": aliases,
        "prompt_chars": len(prompt), "estimated_input_tokens": estimate_tokens(system_prompt) + estimate_tokens(prompt),
        "unprojected_input_tokens": estimate_tokens(system_prompt) + estimate_tokens(original),
        "blocks": {name: {"chars": len(text), "estimated_tokens": estimate_tokens(text) if text else 0}
                   for name, text in blocks.items()},
    }
    return prompt, metrics


def request_metrics(base: dict[str, Any], prompt: str, system_prompt: str) -> dict[str, Any]:
    extra = len(prompt) - base["prompt_chars"]
    estimated = estimate_tokens(system_prompt) + estimate_tokens(prompt)
    return {**base, "prompt_chars": len(prompt),
            "estimated_input_tokens": estimated,
            "unprojected_input_tokens": base["unprojected_input_tokens"] + estimated - base["estimated_input_tokens"],
            "blocks": {**base["blocks"], "protocol_recovery": {
                "chars": extra, "estimated_tokens": estimate_tokens(prompt[-extra:]) if extra else 0}}}


def response_usage(response: dict[str, Any]) -> dict[str, int]:
    raw = response.get("raw", {})
    usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    if not isinstance(usage, dict):
        return {}
    result = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens",
                                         "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
              if type(usage.get(key)) is int and usage[key] >= 0}
    details = usage.get("prompt_tokens_details", {})
    if isinstance(details, dict) and type(details.get("cached_tokens")) is int and details["cached_tokens"] >= 0:
        result["cached_input_tokens"] = details["cached_tokens"]
    return result
