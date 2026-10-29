from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.evaluation.budget import BudgetedProvider, EvalBudget
from app.evaluation.quality import evaluate_run
from app.execution.action_consistency import admit_action, build_consistency_evidence
from app.execution.dynamic_state import state_coder_audit_clause
from app.execution.completion import goal_completed
from app.execution.engine import WorkflowEngine
from app.execution.scene_control import normalize_scene_contract, scene_audit_instruction
from app.models import NodeRunRecord, RunEventRecord, RunRecord
from app.plan_compiler import compile_workflow_from_plan
from app.schemas import PlanCompileRequest, RunRequest
from app.service import get_run_checkpoints, verify_run_audit


SCOPE = {"self_owned": True, "public_only": True}
ACCEPT = {"decision": "accept", "reason": "Explicit offline fixture.", "scope": SCOPE, "findings": []}


class FixtureProvider:
    def __init__(self, *, decision="stop", complete=True, phases=True):
        self.decision, self.complete, self.phases = decision, complete, phases
        self.actor_prompts = []
        self.audit_prompts = []

    async def chat(self, model, system_prompt, user_prompt):
        if "process architect" in system_prompt:
            data = {"intent_mode": "roleplay", "goal": "Resolve the final confrontation.",
                    "roles": [{"name": name, "count": 1, "entity_type": "individual"} for name in ("Aldric", "Reginald")],
                    "environment": {"scenario": "Both are already present in the throne hall.",
                                    "time_context": "Midnight", "spatial_context": "Throne hall"},
                    "simulation_design": {"phase_logic": ["Resolve this existing confrontation."]},
                    "task_contract": {"objective": "Resolve the final confrontation.", "completion_condition": "A decisive outcome exists."}}
            if self.phases:
                data["phases"] = [{"name": "Final confrontation", "objective": "Resolve the existing conflict.",
                                   "exit_criteria": "The core conflict is resolved."}]
        elif "route attention" in system_prompt:
            data = {"active_node_ids": ["agent_1", "agent_2"]}
        elif "action consistency checker" in system_prompt:
            data = ACCEPT
        elif "continuity auditor" in system_prompt:
            self.audit_prompts.append(user_prompt)
            data = {"decision": self.decision, "task_complete": self.complete,
                    "completion_reason": "The actors reached a resolution." if self.complete else "The demand remains unanswered.",
                    "state_patch": {"schema_ops": [], "state_ops": []}}
        elif "autonomous participant" in system_prompt:
            self.actor_prompts.append(user_prompt)
            data = {"participation": "act", "action": "I offer my own answer.", "intent": "Respond without deciding for anyone else."}
        else:
            return {"content": "A factual outcome from the accepted actions."}
        return {"content": json.dumps(data), "raw": {"usage": {"prompt_tokens": 20, "completion_tokens": 10}}}


def compile_fixture(provider):
    return asyncio.run(compile_workflow_from_plan(provider, PlanCompileRequest(
        plan_text="Only the ending of the existing confrontation, not a new opening.", mode="roleplay", max_agents=2))).workflow


@pytest.mark.parametrize("phases", [True, False])
def test_compiler_preserves_coordinates_and_model_scope_without_full_story_defaults(phases):
    workflow = compile_fixture(FixtureProvider(phases=phases))
    assert workflow.environment.time_context == "Midnight"
    assert workflow.environment.spatial_context == "Throne hall"
    assert len(workflow.environment.simulation["phases"]) == 1
    phase = workflow.environment.simulation["phases"][0]
    assert "Resolve" in phase["objective"]
    assert phase["name"] != "Story Setup"


@pytest.mark.parametrize("decision,complete,cap,status,reason", [
    ("continue", False, 2, "stopped", "action_limit"),
    ("continue", False, 4, "stopped", "round_limit"),
    ("stop", False, 2, "stopped", "director_stopped"),
    ("stop", None, 2, "stopped", "director_stopped"),
    ("stop", True, 2, "succeeded", "goal_completed"),
])
def test_real_engine_canonical_context_and_terminal_semantics(decision, complete, cap, status, reason):
    provider = FixtureProvider(decision=decision, complete=complete)
    workflow = compile_fixture(provider)
    workflow.environment.simulation.update(horizon_rounds=1, output_contract={"unit": "action", "max_items": cap})
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider).run(db, workflow, RunRequest()))
        run = db.get(RunRecord, run_id)
        assert run.status == status
        assert json.loads(run.output_json)["completion"]["reason"] == reason
        first = db.query(NodeRunRecord).filter_by(run_id=run_id, node_id="agent_1").one()
        packet = json.loads(first.input_json)["context"]
        assert {row["node_id"] for row in packet["communication"]["known_participants"]} == {"agent_1", "agent_2"}
        assert packet["environment"]["spatial_context"] == "Throne hall"
        prompt = provider.actor_prompts[0]
        assert prompt.count("Name/role: Aldric") == 1
        assert "Entity definition:" not in prompt and "Simulation State:" not in prompt
        assert "Private experience belongs only in private fields" in prompt
        assert "EXECUTION ALLOWANCE" in provider.audit_prompts[0]
        assert f'"remaining_actions": {cap - 2}' in provider.audit_prompts[0]
        terminal = db.query(RunEventRecord).filter_by(run_id=run_id, node_id="run_control").all()
        assert terminal[-1].event == status
        assert all(row.node_id != "run_control" for row in get_run_checkpoints(db, run_id).checkpoints)
        assert verify_run_audit(db, run_id).verified
    database.dispose()


def test_stop_without_goal_completion_is_not_rewarded():
    result = evaluate_run({"status": "stopped", "node_runs": []}, {"require_director_completion": True}, [
        {"event": "episode_audited", "payload": {"decision": "stop", "task_complete": False,
         "completion_reason": "Cannot make further progress."}}])
    assert result["checks"]["director_confirmed_completion"] is False


@pytest.mark.parametrize("reason", [None, "   ", {"claim": "done"}])
def test_completion_requires_a_real_nonempty_assessment(reason):
    assert goal_completed({"decision": "stop", "task_complete": True, "completion_reason": reason}) is False


def test_audit_and_coder_share_one_action_body_with_exact_references():
    actions = [{"node_id": "a", "event_seq": 12, "action_index": 1, "round": 1, "action": "UNIQUE_ACTION_BODY"}]
    prompt = scene_audit_instruction(state={}, scene_control={}, recent_actions=actions, round_index=1, max_actions=4)
    prompt += state_coder_audit_clause(actions, actions_in_audit=True)
    assert prompt.count("UNIQUE_ACTION_BODY") == 1
    assert '"event_seq": 12' in prompt.split("UNCODED ACTION REFS", 1)[1]


def test_scope_conflict_rewrites_only_the_owner_and_never_publishes_the_rejected_text():
    records, generated = [], []

    class Checker:
        async def chat(self, **kwargs):
            candidate = json.loads(kwargs["user_prompt"])["CANDIDATE"]
            assert candidate["actor_id"] == "a"
            result = ACCEPT if len(generated) == 2 else {
                "decision": "conflict", "reason": "The candidate writes another participant's reply.",
                "scope": {"self_owned": False, "public_only": True}, "findings": [
                    {"category": "causality", "reason": "B is an independent role.", "evidence_ids": ["entity.b"]}]}
            return {"content": json.dumps(result)}

    async def generate(packet):
        generated.append(packet)
        return {"participation": "act", "action": "B agrees to everything." if len(generated) == 1 else "I ask B for an answer."}

    result = asyncio.run(admit_action(provider=Checker(), runtime_input={"role": {"node_id": "a"},
        "context": {"round": 1, "action_index": 1}}, evidence=[{"id": "entity.b"}],
        generate=generate, record=records.append, can_regenerate=True))
    assert result["action"] == "I ask B for an answer."
    assert [row["assessment"]["decision"] for row in records] == ["conflict", "accept"]


def test_checker_keeps_received_old_messages_when_the_global_window_has_moved_on():
    workflow = compile_fixture(FixtureProvider())
    evidence = build_consistency_evidence(workflow, {}, [], {"context": {
        "recent_actions": [{"event_seq": 1, "action": "OLD_DELIVERED_CONTENT"}]}})
    actual = next(row["value"] for row in evidence if row["id"] == "actor.input")
    assert actual["context"]["received_actions"][0]["action"] == "OLD_DELIVERED_CONTENT"


def test_eval_exposes_remaining_allowance_without_private_configuration():
    provider = BudgetedProvider(FixtureProvider(), EvalBudget(max_calls=4))
    result = provider.remaining_budget()
    assert result["remaining_calls"] == 4
    assert result["remaining_estimated_cost_usd"] == provider.budget.max_cost_usd
    assert set(result) == {"remaining_calls", "remaining_tokens", "remaining_estimated_cost_usd"}


def test_fallback_scope_is_neutral_without_overriding_explicit_micro_detail():
    assert normalize_scene_contract(None, {})["progress_scale"] == "meso"
    assert normalize_scene_contract({"progress_scale": "micro", "detail": "high"}, {})["progress_scale"] == "micro"


def test_eval_attributes_usage_without_storing_prompt_or_private_material():
    provider = BudgetedProvider(FixtureProvider(), EvalBudget(max_calls=4))
    asyncio.run(provider.chat("", "You are an action consistency checker", "PRIVATE_INPUT"))
    row = provider.usage_by_kind["action_check"]
    assert row == {"calls": 1, "input_tokens": 20, "output_tokens": 10, "failed": 0}
    assert "PRIVATE_INPUT" not in json.dumps(provider.usage_by_kind)
