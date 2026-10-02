from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..execution.context_book import build_scene_context_pack, context_for_node, estimate_tokens
from ..execution.entity_contracts import entity_action_contract
from ..schemas import EnvironmentSpec, WorkflowNode


def build_long_horizon_checkpoints(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    environment = EnvironmentSpec.model_validate(scenario.get("environment", {}))
    nodes = {
        str(raw["id"]): WorkflowNode.model_validate(
            {
                "id": raw["id"],
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "config": {
                    "entity_name": raw.get("name", raw["id"]),
                    "entity_type": raw.get("entity_type", "individual"),
                    "entity_profile": raw.get("profile", ""),
                    "role": raw.get("role", "participant"),
                },
                "inputs": {},
            }
        )
        for raw in scenario.get("nodes", [])
    }
    memories: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
    recent_actions: list[dict[str, Any]] = []
    timeline = {int(item["turn"]): item for item in scenario.get("timeline", [])}
    probes_by_turn: dict[int, list[dict[str, Any]]] = {}
    for probe in scenario.get("probes", []):
        probes_by_turn.setdefault(int(probe["turn"]), []).append(probe)

    checkpoints: list[dict[str, Any]] = []
    total_turns = max(1, int(scenario.get("turns", 1)))
    node_ids = list(nodes)
    for turn in range(1, total_turns + 1):
        step = timeline.get(turn, {})
        event = str(step.get("event") or f"Routine interval {turn}; no durable change is confirmed.")
        active_nodes = [str(value) for value in step.get("active_nodes", [node_ids[(turn - 1) % len(node_ids)]]) if str(value) in nodes]
        if not active_nodes:
            active_nodes = [node_ids[(turn - 1) % len(node_ids)]]
        action = {"turn": turn, "active_node_ids": active_nodes, "event": event}
        recent_actions.append(action)

        for raw in step.get("writes", []):
            memory = {
                "id": str(raw["id"]),
                "kind": str(raw.get("kind", "fact")),
                "content": str(raw["content"]),
                "source_turn": turn,
                "scope": str(raw.get("scope", "node")),
            }
            targets = node_ids if memory["scope"] == "global" else [str(raw.get("node_id", ""))]
            for target in targets:
                if target in memories:
                    memories[target].append(memory)

        if turn not in probes_by_turn:
            continue
        state = {
            "episode_index": turn,
            "shared_context": event,
            "world_state": {"turn": turn, "active_node_ids": active_nodes},
            "phases": [{"id": "long_horizon", "name": "Long horizon", "objective": "Preserve durable dependencies."}],
            "current_phase_index": 0,
        }
        pack = build_scene_context_pack(environment=environment, state=state, recent_actions=recent_actions)
        for probe in probes_by_turn[turn]:
            node_id = str(probe["node_id"])
            node = nodes[node_id]
            visible_background = context_for_node(pack, node)
            node_memories = list(memories[node_id])
            packet = {
                "turn": turn,
                "event": event,
                "active_node_ids": active_nodes,
                "role": {
                    "node_id": node_id,
                    "name": node.config.get("entity_name", node_id),
                    "role": node.config.get("role", "participant"),
                    "profile": node.config.get("entity_profile", ""),
                    "entity_type": node.config.get("entity_type", "individual"),
                    "entity_action_contract": entity_action_contract(str(node.config.get("entity_type", "individual"))),
                },
                "memory": node_memories,
                "background": visible_background,
                "recent_actions": recent_actions[-8:],
            }
            checkpoints.append(
                {
                    "id": str(probe["id"]),
                    "turn": turn,
                    "node_id": node_id,
                    "question": str(probe["question"]),
                    "required_terms": [str(value) for value in probe.get("required_terms", [])],
                    "forbidden_terms": [str(value) for value in probe.get("forbidden_terms", [])],
                    "required_background_ids": [str(value) for value in probe.get("required_background_ids", [])],
                    "forbidden_background_ids": [str(value) for value in probe.get("forbidden_background_ids", [])],
                    "required_memory_ids": [str(value) for value in probe.get("required_memory_ids", [])],
                    "packet": packet,
                    "estimated_tokens": estimate_tokens(json.dumps(packet, ensure_ascii=False, default=str)),
                }
            )
    return checkpoints


def evaluate_long_horizon(
    scenario: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    responses: dict[str, str] | None = None,
) -> dict[str, Any]:
    responses = responses or {}
    max_context_tokens = int(scenario.get("max_context_tokens", 2400))
    checks: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        background_ids = {
            str(entry.get("id"))
            for entry in checkpoint["packet"].get("background", {}).get("entries", [])
            if isinstance(entry, dict)
        }
        memory_ids = {
            str(entry.get("id"))
            for entry in checkpoint["packet"].get("memory", [])
            if isinstance(entry, dict)
        }
        response = responses.get(checkpoint["id"], "")
        required_terms = checkpoint["required_terms"]
        forbidden_terms = checkpoint["forbidden_terms"]
        mechanical = {
            "background_available": set(checkpoint["required_background_ids"]).issubset(background_ids),
            "background_not_overactivated": not set(checkpoint["forbidden_background_ids"]).intersection(background_ids),
            "durable_memory_available": set(checkpoint["required_memory_ids"]).issubset(memory_ids),
            "private_information_isolated": not any(term.casefold() in json.dumps(checkpoint["packet"], ensure_ascii=False).casefold() for term in forbidden_terms),
            "context_within_budget": int(checkpoint["estimated_tokens"]) <= max_context_tokens,
        }
        model_checks: dict[str, bool] = {}
        if checkpoint["id"] in responses:
            folded = response.casefold()
            parsed = _response_json(response)
            decision = str(parsed.get("decision", "")).strip()
            model_checks = {
                "response_json_valid": bool(parsed),
                "required_terms_used": all(term.casefold() in folded for term in required_terms),
                "forbidden_terms_not_leaked": not any(term.casefold() in folded for term in forbidden_terms),
                "decision_not_instruction_echo": bool(decision)
                and _normalize(decision) != _normalize(str(checkpoint["question"])),
            }
        checks.append(
            {
                "id": checkpoint["id"],
                "turn": checkpoint["turn"],
                "node_id": checkpoint["node_id"],
                "estimated_tokens": checkpoint["estimated_tokens"],
                "checks": {**mechanical, **model_checks},
                "response": response,
            }
        )

    flat = [passed for item in checks for passed in item["checks"].values()]
    failures = [f"{item['id']}: {name}" for item in checks for name, passed in item["checks"].items() if not passed]
    participation = Counter(
        node_id
        for item in scenario.get("timeline", [])
        for node_id in item.get("active_nodes", [])
    )
    return {
        "score": round(sum(1 for passed in flat if passed) / len(flat) * 100, 1) if flat else 0.0,
        "mode": "real" if responses else "simulated",
        "failures": failures,
        "checkpoints": checks,
        "stats": {
            "turns": int(scenario.get("turns", 0)),
            "checkpoint_count": len(checkpoints),
            "max_checkpoint_tokens": max((item["estimated_tokens"] for item in checkpoints), default=0),
            "explicit_participation": dict(participation),
        },
    }


def probe_prompt(checkpoint: dict[str, Any]) -> str:
    return (
        "Act only as the specified node and follow its entity_action_contract. Produce the entity's actual in-world response, "
        "not a paraphrase of the question. Use only the supplied checkpoint context. Earlier facts matter only when they are "
        "present in your memory or visible background. Do not infer or reveal another node's private information. Return "
        "JSON only with keys decision, remembered_constraints, rationale, and unknowns. Preserve exact identifiers when a "
        "constraint contains one.\n\n"
        f"QUESTION:\n{checkpoint['question']}\n\n"
        f"CHECKPOINT:\n{json.dumps(checkpoint['packet'], ensure_ascii=False, default=str)}"
    )


def _response_json(raw: str) -> dict[str, Any]:
    candidate = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _normalize(value: str) -> str:
    return " ".join(value.casefold().strip().rstrip(".?!").split())
