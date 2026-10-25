from __future__ import annotations

import json
from typing import Any


def contains_marker(text: str, requirement: dict[str, Any]) -> bool:
    return any(str(term).casefold() in text.casefold() for term in [requirement["marker"], *requirement.get("aliases", [])])


def evaluate_behavior(detail: dict[str, Any], contract: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    actors = set(contract.get("actors", []))
    turns = [row for row in detail.get("node_runs", []) if row.get("node_id") in actors and row.get("status") == "succeeded"]
    leaks = []
    for secret in contract.get("secrets", []):
        marker = str(secret["marker"]).casefold()
        for index, turn in enumerate(turns, 1):
            node_id = turn["node_id"]
            if node_id != secret["owner"] and marker in json.dumps(turn.get("input", {})).casefold():
                leaks.append(f"turn {index}: {node_id} received private marker {secret['marker']}")
            if marker in str(turn.get("output", {}).get("action", "")).casefold():
                leaks.append(f"turn {index}: {node_id} published private marker {secret['marker']}")
    allowed = contract.get("allowed_sources", {})
    for index, turn in enumerate(turns, 1):
        context = turn.get("input", {}).get("context", {})
        sources = {row.get("node_id") for row in context.get("recent_actions", [])} - {turn["node_id"]}
        if turn["node_id"] in allowed and sources - set(allowed[turn["node_id"]]):
            leaks.append(f"turn {index}: {turn['node_id']} received a disallowed source")

    propagation = []
    for requirement in contract.get("propagation", []):
        path = requirement["path"]
        missing = []
        premature = []
        previous = -1
        for hop, node_id in enumerate(path):
            candidates = [(index, turn) for index, turn in enumerate(turns)
                          if turn["node_id"] == node_id and contains_marker(str(turn.get("output", {}).get("action", "")), requirement)]
            if not candidates:
                missing.append(node_id)
                continue
            index, turn = candidates[0]
            if hop:
                inbox = turn.get("input", {}).get("_interactions", [])
                delivered = any(message.get("source") == path[hop - 1]
                                and contains_marker(str(message.get("message", "")), requirement) for message in inbox)
                if index <= previous or previous < 0 or not delivered:
                    premature.append(node_id)
            previous = index
        propagation.append({"marker": requirement["marker"], "status": "fail" if premature else "inconclusive" if missing else "pass",
                            "missing": missing, "premature": premature})

    rounds = {turn.get("output", {}).get("round") for turn in turns} - {None}
    autonomy = [{"node": turn["node_id"], "role": str(turn.get("input", {}).get("role", {}).get("role", "")),
                 "action_index": turn.get("output", {}).get("action_index"),
                 "received_event_seqs": [item["event_seq"] for item in turn.get("input", {}).get("context", {}).get("recent_actions", [])
                                         if item.get("node_id") != turn["node_id"] and isinstance(item.get("event_seq"), int)],
                 "action": str(turn.get("output", {}).get("action", "")),
                 "intent": str(turn.get("output", {}).get("intent", ""))} for turn in turns]
    guidance = [event.get("payload", {}).get("guidance", "") for event in events if event.get("event") == "episode_audited"]
    failed = bool(leaks) or any(row["status"] == "fail" for row in propagation)
    boundary_status = "fail" if leaks else "pass" if actors.issubset({turn['node_id'] for turn in turns}) else "inconclusive"
    incomplete = boundary_status == "inconclusive" or any(row["status"] == "inconclusive" for row in propagation)
    simulation = detail.get("output", {}).get("simulation", {})
    dynamic = simulation.get("dynamic_state", {})
    state_evidence = [{"id": concept_id, "value": item.get("value"), "sources": item.get("source_refs", []),
                       "visibility": dynamic.get("concepts", {}).get(concept_id, {}).get("visibility")}
                      for concept_id, item in dynamic.get("values", {}).items() if isinstance(item, dict)]
    return {
        "status": "fail" if failed else "inconclusive" if incomplete else "review_required",
        "information_boundary": {"status": boundary_status, "findings": leaks},
        "propagation": propagation,
        "long_horizon": {"status": ("review_required" if detail.get("status") == "succeeded" else "incomplete")
                         if len(rounds) >= int(contract.get("long_horizon_rounds", 24)) else "not_tested",
                         "observed_rounds": len(rounds)},
        "autonomy": {"status": "review_required", "candidates": autonomy, "director_guidance": guidance},
        "coordination": {"routing": [event.get("payload", {}).get("reason", "") for event in events if event.get("event") == "nodes_activated"],
                         "scene_goals": list(dict.fromkeys(str(turn.get("input", {}).get("context", {}).get("scene_contract", {}).get("goal", "")) for turn in turns)),
                         "roster": {row["node"]: row["role"] for row in autonomy}},
        "state_snapshot": {"available": "dynamic_state" in simulation, "run_complete": detail.get("status") == "succeeded"},
        "state_evidence": state_evidence,
        "misunderstanding": {"status": "review_required", "question": contract.get("misunderstanding_review", "")},
    }
