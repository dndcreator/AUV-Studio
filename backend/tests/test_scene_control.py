from __future__ import annotations

import asyncio
import copy
import json

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.checkpoints import continuous_snapshot, restore_continuous_checkpoint
from app.execution.director_policy import assess_output_quality
from app.execution.engine import WorkflowEngine
from app.execution.scene_control import apply_scene_audit, init_scene_control, normalize_scene_contract, scene_audit_instruction, scene_progress_feedback
from app.execution.action_output import normalize_action_output
from app.evaluation.quality import evaluate_run
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
                content = {"decision": "stop" if self.round == 2 else "continue", "state_patch": {"schema_ops": [], "state_ops": []},
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


def goal_judgment(round_index, progress="none", stagnant=False):
    return {"round": round_index, "stagnant": stagnant, "reason": "New dialogue occurred.",
            "goal_progress": progress, "goal_reason": "Compared accepted actions with the actual exit conditions."}


def test_goal_progress_is_independent_of_novelty_and_feedback_escalates_without_rewriting_contract():
    current = {**init_scene_control(), "contract": copy.deepcopy(CONTRACT)}
    for round_index in range(1, 7):
        current, assessment = apply_scene_audit(current, {"decision": "continue", "round_progress": [goal_judgment(round_index)]},
                                                state={}, round_index=round_index)
        assert current["stagnation"] == 0
        assert current["goal_stagnation"] == round_index
        assert assessment["unassessed_goal_rounds"] == []
        assert current["contract"] == CONTRACT
        feedback = scene_progress_feedback(current)
        assert feedback.get("level") == (None if round_index == 1 else "refocus" if round_index < 4 else "reassess" if round_index < 6 else "consider_stop")
    advanced, _ = apply_scene_audit(current, {"round_progress": [goal_judgment(7, "advanced")]}, state={}, round_index=7)
    assert advanced["goal_stagnation"] == 0 and scene_progress_feedback(advanced) == {}
    transitioned, assessment = apply_scene_audit(current, {"decision": "transition", "scene_contract": CONTRACT,
        "round_progress": [goal_judgment(7)]}, state={}, round_index=7)
    assert transitioned["goal_stagnation"] == 0 and scene_progress_feedback(transitioned) == {}
    assert assessment["goal_stagnation"] == 7


@pytest.mark.parametrize("patch", [
    {"goal_progress": "uncertain"}, {"goal_progress": []}, {"goal_progress": "advanced", "goal_reason": ""},
    {"round": 1}, {"round": 99}, {"stagnant": "false"},
])
def test_missing_invalid_or_replayed_goal_judgments_are_unassessed_not_success_or_stagnation(patch):
    current = {**init_scene_control(), "last_audited_round": 1, "goal_stagnation": 1}
    result, assessment = apply_scene_audit(current, {"round_progress": [{**goal_judgment(2), **patch}]}, state={}, round_index=2)
    assert result["goal_stagnation"] == 0
    assert assessment["unassessed_goal_rounds"] == [2]
    assert assessment["goal_needs_attention"] is False


def test_micro_progress_preserves_slow_detail_and_prompt_requires_evidence_not_word_count():
    current = {**init_scene_control(), "contract": copy.deepcopy(CONTRACT)}
    for round_index in range(1, 5):
        current, _ = apply_scene_audit(current, {"round_progress": [goal_judgment(round_index, "advanced")]}, state={}, round_index=round_index)
        assert scene_progress_feedback(current) == {}
        assert current["contract"] == CONTRACT
    prompt = scene_audit_instruction(state={}, scene_control=current, recent_actions=[], round_index=5, max_actions=20)
    assert "goal_progress SEPARATELY from novelty" in prompt
    assert "do not fabricate pressure" in prompt
    assert "at the contracted progress_scale" in prompt


def test_duplicate_conflicting_round_judgments_cannot_establish_goal_progress_or_stagnation():
    result, assessment = apply_scene_audit(init_scene_control(), {"round_progress": [
        goal_judgment(1, "advanced"), goal_judgment(1, "none"), goal_judgment(2, "none"),
    ]}, state={}, round_index=2)
    assert assessment["unassessed_goal_rounds"] == assessment["unassessed_rounds"] == [1]
    assert result["goal_stagnation"] == 1


def test_effect_evaluation_marks_missing_goal_assessments_as_missing_coverage():
    result = evaluate_run({"status": "succeeded", "node_runs": []}, {"require_scene_control": True}, [{
        "event": "episode_audited", "payload": {"progress_assessment": {
            "round_progress": [{"round": 1, "stagnant": False}], "unassessed_rounds": [],
        }},
    }])
    assert result["checks"]["director_assesses_each_round"] is True
    assert result["checks"]["director_assesses_goal_progress"] is False
    assert any("goal progress" in finding for finding in result["findings"])


def test_real_engine_supplies_goal_feedback_and_rolls_back_the_counter_without_extra_calls_or_invented_completion():
    class Provider:
        def __init__(self):
            self.round = 0
            self.calls = 0
            self.actor_feedback = []

        async def chat(self, model, system_prompt, user_prompt):
            self.calls += 1
            if "action consistency checker" in system_prompt:
                content = {"decision": "accept", "reason": "Offline fixture.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
            elif "route attention" in system_prompt:
                self.round += 1
                content = {"active_node_ids": ["a", "b"], "scene_contract": CONTRACT}
                if self.round >= 3:
                    assert '"level": "refocus"' in user_prompt or '"level": "reassess"' in user_prompt
            elif "continuity auditor" in system_prompt:
                content = {"decision": "stop" if self.round == 6 else "continue", "task_complete": False,
                           "completion_reason": "Still no grounded path to completion.", "round_progress": [goal_judgment(self.round)],
                           "state_patch": {"schema_ops": [], "state_ops": []}}
            else:
                self.actor_feedback.append((self.round, '"level": "refocus"' in user_prompt, '"level": "reassess"' in user_prompt))
                content = {"participation": "act", "action": "I offer another explanation, not a resolution.", "intent": "PRIVATE-PLAN", "turn_delta": "A different explanation."}
            return {"content": json.dumps(content)}

    wf = workflow()
    wf.environment.simulation["horizon_rounds"] = 6
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider).run(db, wf, RunRequest()))
        run = db.get(RunRecord, run_id)
        output = json.loads(run.output_json)
        assert run.status == "stopped" and output["completion"]["status"] == "incomplete"
        assert len(output["actions"]) == 12 and provider.calls == 36
        assert output["scene_control"]["goal_stagnation"] == 6
        assert output["scene_control"]["stagnation"] == 0
        assert "goal_stagnation" not in output["simulation"]
        assert all(refocus for round_index, refocus, _ in provider.actor_feedback if round_index in (3, 4))
        assert all(reassess for round_index, _, reassess in provider.actor_feedback if round_index in (5, 6))
        assert "PRIVATE-PLAN" not in json.dumps(scene_progress_feedback(output["scene_control"]))
        audits = db.query(RunEventRecord).filter_by(run_id=run_id, event="episode_audited").order_by(RunEventRecord.seq).all()
        _, rollback = build_rollback_request(db, run_id, RollbackRequest(event_seq=audits[2].seq))
        restored = {}
        restore_continuous_checkpoint(restored, rollback)
        assert restored["_scene_control"]["goal_stagnation"] == 3
        assert scene_progress_feedback(restored["_scene_control"])["level"] == "refocus"
        assert verify_run_audit(db, run_id).verified
    database.dispose()
