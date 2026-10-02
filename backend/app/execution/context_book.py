from __future__ import annotations

import json
import math
import re
from typing import Any

from ..schemas import BackgroundEntry, EnvironmentSpec, WorkflowNode


_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9_-]{1,}", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]")


def environment_for_runtime(environment: EnvironmentSpec | dict[str, Any]) -> dict[str, Any]:
    """Return legacy environment fields without exposing the complete private book."""
    value = environment.model_dump() if isinstance(environment, EnvironmentSpec) else dict(environment)
    value.pop("context_book", None)
    return value


def build_scene_context_pack(
    *,
    environment: EnvironmentSpec,
    state: dict[str, Any],
    recent_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    query = _build_query(environment=environment, state=state, recent_actions=recent_actions)
    entries = [entry for entry in environment.context_book.entries if entry.enabled and entry.content.strip()]
    selected: list[tuple[int, BackgroundEntry, list[str]]] = []
    for entry in entries:
        score, reasons = _activation_score(entry, query)
        if score > 0:
            selected.append((score, entry, reasons))

    selected.sort(key=lambda item: (item[0], item[1].priority), reverse=True)
    by_id = {entry.id: entry for entry in entries}
    chosen: list[tuple[BackgroundEntry, list[str]]] = []
    chosen_ids: set[str] = set()
    budget = environment.context_book.token_budget
    used = 0

    def add(entry: BackgroundEntry, reasons: list[str]) -> bool:
        nonlocal used
        if entry.id in chosen_ids:
            return True
        cost = estimate_tokens(entry.content)
        if chosen and used + cost > budget:
            return False
        chosen.append((entry, reasons))
        chosen_ids.add(entry.id)
        used += cost
        return True

    for _, entry, reasons in selected:
        if not add(entry, reasons):
            continue
        # One-hop links provide predictable lore expansion without recursive cascades.
        for related_id in entry.activation.related_entry_ids:
            related = by_id.get(related_id)
            if related is not None and related.enabled:
                add(related, [f"related:{entry.id}"])

    episode = int(state.get("episode_index", 0) or 0) if isinstance(state, dict) else 0
    return {
        "episode": episode,
        "token_budget": budget,
        "estimated_tokens": used,
        "entries": [_pack_entry(entry, reasons) for entry, reasons in chosen],
    }


def context_for_node(pack: dict[str, Any], node: WorkflowNode) -> dict[str, Any]:
    entries = pack.get("entries", []) if isinstance(pack, dict) else []
    visible: list[dict[str, Any]] = []
    for raw in entries if isinstance(entries, list) else []:
        if not isinstance(raw, dict):
            continue
        visibility = raw.get("visibility", {})
        if not isinstance(visibility, dict) or visibility.get("scope", "global") == "global":
            visible.append(raw)
            continue
        node_ids = visibility.get("node_ids", [])
        if isinstance(node_ids, list) and node.id in {str(value) for value in node_ids}:
            visible.append(raw)
    return {
        "episode": pack.get("episode", 0),
        "estimated_tokens": sum(estimate_tokens(str(entry.get("content", ""))) for entry in visible),
        "entries": visible,
    }


def activation_trace(pack: dict[str, Any]) -> dict[str, Any]:
    entries = pack.get("entries", []) if isinstance(pack, dict) else []
    return {
        "episode": pack.get("episode", 0),
        "entry_count": len(entries) if isinstance(entries, list) else 0,
        "estimated_tokens": pack.get("estimated_tokens", 0),
        "entries": [
            {
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "kind": item.get("kind", "background"),
                "reasons": item.get("activation_reasons", []),
            }
            for item in entries
            if isinstance(item, dict)
        ],
    }


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    non_cjk = max(0, len(text) - cjk)
    return max(1, cjk + math.ceil(non_cjk / 4))


def _build_query(
    *, environment: EnvironmentSpec, state: dict[str, Any], recent_actions: list[dict[str, Any]]
) -> str:
    payload = {
        "profile": environment.profile,
        "scenario": environment.scenario,
        "time": environment.time_context,
        "space": environment.spatial_context,
        "phase": _current_phase(state),
        "shared_context": state.get("shared_context", "") if isinstance(state, dict) else "",
        "world_state": state.get("world_state", {}) if isinstance(state, dict) else {},
        "recent_actions": recent_actions[-8:],
    }
    return json.dumps(payload, ensure_ascii=False, default=str).casefold()


def _current_phase(state: dict[str, Any]) -> dict[str, Any]:
    phases = state.get("phases", []) if isinstance(state, dict) else []
    index = int(state.get("current_phase_index", 0) or 0) if isinstance(state, dict) else 0
    if isinstance(phases, list) and 0 <= index < len(phases) and isinstance(phases[index], dict):
        return phases[index]
    return {}


def _activation_score(entry: BackgroundEntry, query: str) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = entry.priority
    if entry.activation.always:
        reasons.append("always")
        score += 1000
    matched = [key for key in entry.activation.keywords if key.strip() and key.strip().casefold() in query]
    if matched:
        reasons.extend([f"keyword:{key}" for key in matched[:4]])
        score += 200 + len(matched) * 20
    hint_terms = _terms(entry.activation.semantic_hint)
    overlap = [term for term in hint_terms if term in query]
    if overlap:
        reasons.append("semantic_hint")
        score += min(120, len(overlap) * 15)
    return (score, reasons) if reasons else (0, [])


def _terms(text: str) -> set[str]:
    folded = text.casefold()
    terms = set(_ASCII_TERM.findall(folded))
    for phrase in re.findall(r"[\u3400-\u9fff]{2,}", folded):
        terms.add(phrase)
        terms.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return terms


def _pack_entry(entry: BackgroundEntry, reasons: list[str]) -> dict[str, Any]:
    return {
        "id": entry.id,
        "title": entry.title or entry.content[:32],
        "content": entry.content,
        "kind": entry.kind,
        "source": entry.source,
        "visibility": entry.visibility.model_dump(),
        "activation_reasons": reasons,
    }
