from __future__ import annotations

import asyncio
import copy
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.checkpoints import continuous_snapshot, restore_continuous_checkpoint
from app.execution.director_policy import assess_output_quality
from app.execution.engine import WorkflowEngine
from app.execution.scene_control import apply_scene_audit, init_scene_control, normalize_scene_contract, scene_audit_instruction
from app.execution.action_output import normalize_action_output
from app.execution.simulation_state import init_simulation_state
from app.models import NodeRunRecord, RunEventRecord, RunRecord
from app.schemas import RollbackRequest, RunRequest, WorkflowDefinition
from app.service import build_rollback_request, get_run_checkpoints, verify_run_audit


CONTRACT = {
    "goal": "Decide whether to share an umbrella without prescribing either response.",
    "pacing": "slow", "detail": "high", "progress_scale": "micro",
    "exit_conditions": ["An offer receives a response."],
}


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "scene_test", "name": "scene test", "version": 1,
        "nodes": [
            {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0},
             "config": {"entity_type": "individual", "entity_name": node_id}, "inputs": {}}
            for node_id, node_type in [("director", "director"), ("a", "agent"), ("b", "agent")]
        ],
        "edges": [], "entry_nodes": ["director"],
        "environment": {"simulation": {
            "mode": "roleplay", "execution_model": "continuous", "horizon_rounds": 4,
            "director_interval_rounds": 1, "detail_granularity": "detailed",
            "dynamic_state": {"enabled": False},
            "output_contract": {"min_items": 20, "max_items": 20, "unit": "action"},
            "phases": [{"id": "one", "name": "Shelter", "objective": "An offer"},
                       {"id": "two", "name": "Departure", "objective": "Choose a route"}],
        }},
    })


def test_contract_normalization_accepts_open_goals_but_rejects_invalid_controls() -> None:
    state = init_simulation_state(workflow())
    result = normalize_scene_contract({"pacing": {}, "detail": [], "progress_scale": None, "exit_conditions": []}, state)
    assert result["goal"] == "An offer"
    assert result["pacing"] == "steady"
    assert result["detail"] == "high"
    assert result["exit_conditions"]


def test_stagnation_counts_semantic_rounds_and_preserves_small_progress() -> None:
    current = {**init_scene_control(), "contract": CONTRACT}
    control = {"decision": "continue", "round_progress": [
        {"round": 1, "stagnant": True, "reason": "Two speakers repeat their positions."},
        {"round": 2, "stagnant": True, "reason": "Claimed novelty is not in actual actions."},
    ]}
    result, assessment = apply_scene_audit(current, control, state={}, round_index=2)
    assert result["stagnation"] == 2
    assert assessment["needs_attention"] is True
    assert result["contract"] == CONTRACT
    next_result, assessment = apply_scene_audit(result, {
        "decision": "continue", "round_progress": [{"round": 3, "stagnant": False, "reason": "A small new response matters at micro scale."}],
        "scene_contract": {"goal": "Hijacked goal", "exit_conditions": ["Forced outcome"], "pacing": "fast"},
    }, state={}, round_index=3)
    assert next_result["stagnation"] == 0
    assert next_result["contract"]["goal"] == CONTRACT["goal"]
    assert next_result["contract"]["exit_conditions"] == CONTRACT["exit_conditions"]
    assert next_result["contract"]["pacing"] == "fast"


def test_missing_or_replayed_judgments_cannot_fabricate_a_stagnation_streak() -> None:
    current = {**init_scene_control(), "contract": CONTRACT, "last_audited_round": 1, "stagnation": 1}
    result, assessment = apply_scene_audit(current, {"round_progress": [
        {"round": 1, "stagnant": True}, {"round": 2, "stagnant": "true"},
        {"round": 3, "stagnant": True}, {"round": 99, "stagnant": True},
    ]}, state={}, round_index=3)
    assert result["stagnation"] == 1
    assert assessment["needs_attention"] is False
    assert assessment["unassessed_rounds"] == [2]


def test_audit_prompt_uses_global_round_ids_in_the_response_example() -> None:
    prompt = scene_audit_instruction(
        state={}, scene_control={"contract": CONTRACT, "last_audited_round": 4},
        recent_actions=[{"round": 5, "action": "A specific response."}], round_index=5, max_actions=10,
    )
    assert '"round":5,"stagnant"' in prompt
    assert "global round IDs: [5]" in prompt
    assert "Do not renumber a later batch from 1" in prompt


def test_transition_renews_contract_and_checkpoint_restores_runtime_control_exactly() -> None:
    state = init_simulation_state(workflow())
    context = {"_simulation": state, "_scene_control": {**init_scene_control(), "contract": copy.deepcopy(CONTRACT), "stagnation": 2}}
    snapshot = continuous_snapshot(context, [], 2, 4, 4)
    result, _ = apply_scene_audit(context["_scene_control"], {
        "decision": "transition", "scene_contract": {**CONTRACT, "goal": "Choose a route"},
    }, state=state, round_index=2)
    assert result["contract"]["goal"] == "Choose a route"
    assert result["stagnation"] == 0
    context["_scene_control"] = result
    restore_continuous_checkpoint(context, RunRequest(input={"_rollback": {"continuous_snapshot": snapshot}}))
    assert context["_scene_control"] == snapshot["scene_control"]
    assert context["_scene_control"]["stagnation"] == 2
    context["_scene_control"]["contract"]["goal"] = "Changed again"
    assert snapshot["scene_control"]["contract"]["goal"] == CONTRACT["goal"]
    assert "scene_control" not in context["_simulation"]


def test_waiting_with_empty_delta_never_leaks_the_protocol_as_readable_action() -> None:
    value = normalize_action_output({"content": '{"participation":"wait","action":"","turn_delta":""}'}, round_index=1, action_index=1)
    assert value["action"] == ""
    assert value["content"] == ""
    assert value["turn_delta"] == ""
    direct = normalize_action_output({"participation": "act", "action": "A new offer.", "turn_delta": "Offer made"}, round_index=1, action_index=2)
    assert direct["turn_delta"] == "Offer made"


def test_quality_does_not_reward_padding_or_require_english_word_boundaries() -> None:
    node = workflow().nodes[1]
    short = assess_output_quality(node=node, runtime_input={}, output={"content": "她说：我决定留下。"}, simulation={"mode": "roleplay"})
    padded = assess_output_quality(node=node, runtime_input={}, output={"content": "她说：我决定留下。" * 100}, simulation={"mode": "roleplay"})
    assert short["score"] == padded["score"]
    assert "output_too_short" not in short["reasons"]


def test_real_engine_protocol_checks_false_delta_without_extra_calls_and_can_stop_early() -> None:
    class Provider:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.round = 0

        async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
            if "action consistency checker" in system_prompt:
                return {"content": '{"decision":"accept","reason":"Offline fixture.","scope":{"self_owned":true,"public_only":true},"findings":[]}'}
            self.prompts.append(user_prompt)
            if "route attention" in system_prompt:
                self.round += 1
                content = {"active_node_ids": ["a", "b"], "scene_contract": CONTRACT}
            elif "continuity auditor" in system_prompt:
                assert "Turn Delta is a participant claim, not proof" in user_prompt
                content = {"decision": "stop" if self.round == 2 else "continue",
                           "task_complete": self.round == 2, "completion_reason": "The fixture scene is resolved.",
                           "round_progress": [{"round": self.round, "stagnant": True, "reason": "The same action repeats despite claimed novelty."}]}
            else:
                content = {"participation": "act", "action": "I repeat my earlier offer.", "intent": "offer shelter", "turn_delta": "Everything has changed."}
            return {"content": json.dumps(content)}

    database = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider).run(db, workflow(), RunRequest(input={})))  # type: ignore[arg-type]
        run = db.get(RunRecord, run_id)
        assert run.status == "succeeded"
        output = json.loads(run.output_json)
        assert len(output["actions"]) == 4  # Twenty is a budget, not a mandatory quota.
        assert output["scene_control"]["stagnation"] == 2
        assert "turn_delta" not in output["simulation"]
        audits = db.query(RunEventRecord).filter_by(run_id=run_id, event="episode_audited").order_by(RunEventRecord.seq).all()
        assert json.loads(audits[-1].payload_json)["progress_assessment"]["needs_attention"] is True
        assert verify_run_audit(db, run_id).verified is True
        checkpoints = get_run_checkpoints(db, run_id)
        assert sum(item.event == "episode_audited" for item in checkpoints.checkpoints) == 2
        assert all(item.summary for item in checkpoints.checkpoints if item.event == "episode_audited")
        _, rollback = build_rollback_request(db, run_id, RollbackRequest(event_seq=audits[0].seq))
        restored: dict = {}
        restore_continuous_checkpoint(restored, rollback)
        assert restored["_scene_control"]["contract"] == CONTRACT
        assert restored["_scene_control"]["stagnation"] == 1
        for row in db.query(NodeRunRecord).filter(NodeRunRecord.node_id.in_(["a", "b"])):
            assert json.loads(row.input_json)["context"]["scene_contract"] == CONTRACT
        assert len(provider.prompts) == 8
    database.dispose()
