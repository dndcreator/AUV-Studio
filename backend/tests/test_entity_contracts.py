from __future__ import annotations

from app.evaluation.long_horizon import build_long_horizon_checkpoints
from app.execution.entity_contracts import entity_action_contract
from app.execution.engine import WorkflowEngine
from app.execution.simulation_loop import action_instruction, build_role_packet
from app.long_horizon_eval import _load_scenario
from app.schemas import WorkflowNode


def _group_node() -> WorkflowNode:
    return WorkflowNode.model_validate(
        {
            "id": "crowd",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "config": {
                "entity_type": "group",
                "entity_name": "Station crowd",
                "entity_profile": "Commuters reacting to a service shutdown.",
            },
            "inputs": {},
        }
    )


def test_group_contract_is_available_without_optional_behavior_prompt() -> None:
    contract = entity_action_contract("group")
    role = build_role_packet(_group_node(), [])

    assert "collective" in contract
    assert "majority" in contract
    assert "minority" in contract
    assert "merely restate the instruction" in contract
    assert role["behavior_rule"] == ""
    assert role["entity_action_contract"] == contract


def test_continuous_action_prompt_prioritizes_entity_contract() -> None:
    role = build_role_packet(_group_node(), [])
    prompt = action_instruction({"state": {}, "memory": []}, role)

    assert "Follow the entity action contract" in prompt
    assert "never a paraphrase" in prompt
    assert "Represent a collective, not one person" in prompt


def test_long_horizon_group_checkpoint_uses_runtime_group_contract() -> None:
    checkpoints = build_long_horizon_checkpoints(_load_scenario())
    crowd = next(item for item in checkpoints if item["id"] == "crowd_privacy")

    assert crowd["packet"]["role"]["entity_type"] == "group"
    assert "aggregate response" in crowd["packet"]["role"]["entity_action_contract"]


def test_continuous_memory_selection_keeps_own_and_global_but_not_peer_facts() -> None:
    node = _group_node()
    memories = [
        {"id": 1, "node_id": "crowd", "scope": "node", "kind": "fact", "importance": 0.7, "content": "own"},
        {"id": 2, "node_id": "other", "scope": "node", "kind": "fact", "importance": 0.9, "content": "peer"},
        {"id": 3, "node_id": "world", "scope": "global", "kind": "fact", "importance": 0.7, "content": "global"},
    ]

    selected = WorkflowEngine._select_memories_for_node(memories, node, include_peer_facts=False)
    assert {item["content"] for item in selected} == {"own", "global"}
