from __future__ import annotations

import asyncio
import json
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.evaluation.behavior import evaluate_behavior
from app.evaluation.scenario import load_scenario
from app.execution.engine import WorkflowEngine
from app.models import NodeRunRecord, RunEventRecord, RunRecord
from app.schemas import RunRequest
from app.service import verify_run_audit
from app.evaluation.long_horizon import build_long_horizon_checkpoints, evaluate_long_horizon
from app.long_horizon_eval import _load_scenario
from app.eval_runner import main
from app.evaluation.budget import EvalBudgetExceeded


def turn(node, action, sources=(), secret=""):
    return {"node_id": node, "status": "succeeded", "input": {"context": {"recent_actions": [{"node_id": source} for source in sources]},
            "_interactions": [{"source": source, "message": action} for source in sources], "private_probe": secret},
            "output": {"action": action, "intent": "I claim initiative", "round": 1}}


def contract():
    return {"actors": ["a", "b", "c"], "secrets": [{"owner": "a", "marker": "PRIVATE-K7"}],
            "allowed_sources": {"a": [], "b": ["a"], "c": ["b"]},
            "propagation": [{"marker": "PUBLIC-816", "path": ["a", "b", "c"]}]}


def test_validation_detects_leakage_and_premature_knowledge_not_just_success():
    rows = [turn("c", "PUBLIC-816", sources=["a"], secret="PRIVATE-K7"), turn("a", "PUBLIC-816")]
    result = evaluate_behavior({"node_runs": rows}, contract(), [])
    assert result["status"] == "fail"
    assert result["information_boundary"]["findings"]
    assert result["propagation"][0]["status"] == "fail"


def test_missing_propagation_is_inconclusive_and_intent_does_not_prove_autonomy():
    result = evaluate_behavior({"node_runs": [turn("a", "PUBLIC-816")]}, contract(), [])
    assert result["information_boundary"]["status"] == "inconclusive"
    assert result["propagation"][0]["status"] == "inconclusive"
    assert result["status"] == "inconclusive"
    assert result["autonomy"]["status"] == "review_required"
    assert result["long_horizon"]["status"] == "not_tested"


def test_causal_propagation_passes_but_requires_semantic_review():
    rows = [turn("a", "PUBLIC-816"), turn("b", "PUBLIC-816", ["a"]), turn("c", "PUBLIC-816", ["b"])]
    result = evaluate_behavior({"node_runs": rows}, contract(), [])
    assert result["information_boundary"]["status"] == "pass"
    assert result["propagation"][0]["status"] == "pass"
    assert result["status"] == "review_required"


def test_explicit_identifier_aliases_do_not_produce_a_false_missing_hop():
    rules = contract()
    rules["propagation"][0]["aliases"] = ["816"]
    rows = [turn("a", "PUBLIC-816"), turn("b", "PUBLIC-816", ["a"]), turn("c", "816", ["b"])]
    assert evaluate_behavior({"node_runs": rows}, rules, [])["propagation"][0]["status"] == "pass"


def test_missing_real_checkpoint_responses_are_not_a_successful_simulation():
    scenario = _load_scenario()
    checkpoints = build_long_horizon_checkpoints(scenario)
    result = evaluate_long_horizon(scenario, checkpoints, {})
    assert result["mode"] == "real"
    assert result["score"] < 100
    assert all(not row["checks"]["response_available"] for row in result["checkpoints"])


def test_aborted_long_run_is_not_a_completed_long_horizon_validation():
    rows = [{**turn("a", f"action {index}"), "output": {"action": f"action {index}", "round": index}} for index in range(1, 25)]
    detail = {"status": "failed", "node_runs": rows, "output": {"simulation": {"dynamic_state": {"values": {}}}}}
    result = evaluate_behavior(detail, contract(), [])
    assert result["long_horizon"]["status"] == "incomplete"
    assert result["state_snapshot"] == {"available": True, "run_complete": False}


@pytest.mark.parametrize("status,code", [("fail", 1), ("inconclusive", 1), ("review_required", 0)])
def test_cli_does_not_treat_missing_coverage_as_success(monkeypatch, status, code):
    async def execute(*_args):
        return {"run": {"status": "succeeded"}, "deterministic_evaluation": {"score": 100,
                "behavior": {"status": status, "long_horizon": {"status": "not_tested"}}},
                "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0}}, "not-written.md"

    monkeypatch.setattr("app.eval_runner._execute", execute)
    assert main(["--scenario", "boundary_autonomy", "--execute", "--max-calls", "15"]) == code


def proposal(concept, value, source, owner=""):
    return {"id": concept, "op": "upsert", "description": "A durable constraint", "scope": "entity" if owner else "global",
            "owner_id": owner, "value_type": "text", "visibility": "private" if owner else "global",
            "retention": {"mode": "persistent"}, "future_relevance": "Constrains later decisions", "reason": "Recorded from a specific action",
            "source_refs": [source]}, {"op": "set", "concept_id": concept, "value": value, "confidence": 1,
                                      "reason": "Recorded from a specific action", "source_refs": [source]}


def test_real_engine_24_rounds_keeps_authority_privacy_and_rejects_unsupported_changes():
    _, workflow = load_scenario("boundary_autonomy")
    workflow.environment.simulation["horizon_rounds"] = 24
    workflow.environment.simulation["dynamic_state"]["max_new_per_round"] = 3
    snapshots = []

    class Provider:
        audits = 0

        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                return {"content": '{"active_node_ids":["mina","ivo","sen"]}'}
            if "continuity auditor" in system_prompt:
                roster, _ = json.JSONDecoder().raw_decode(user_prompt.split("PARTICIPANT ROSTER:\n", 1)[1])
                assert {row["node_id"]: row["role"] for row in roster}["sen"] == "field logistics specialist"
                assert all("profile" not in row for row in roster)
                channels = json.loads(user_prompt.split("COMMUNICATION CHANNELS (empty means shared public scene):\n", 1)[1].split("\n\n", 1)[0])
                assert [(edge["source"], edge["target"]) for edge in channels] == [("mina", "ivo"), ("ivo", "sen")]
                self.audits += 1
                batch, _ = json.JSONDecoder().raw_decode(user_prompt.split("UNCODED ACTIONS:\n", 1)[1])
                first, second, third = batch
                assert first["received_event_seqs"] == []
                assert first["event_seq"] in second["received_event_seqs"]
                assert second["event_seq"] in third["received_event_seqs"]
                assert first["event_seq"] not in third["received_event_seqs"]
                schemas, values = [], []
                if self.audits == 1:
                    source = {key: batch[0][key] for key in ("node_id", "action_index", "event_seq")}
                    for concept, value, owner in [("release_rule", "EARLY-FACT-18", ""), ("mina_secret", "PRIVATE-K7", "mina")]:
                        schema, state = proposal(concept, value, source, owner)
                        schemas.append(schema)
                        values.append(state)
                fake = {"node_id": "nonexistent", "action_index": 9999, "event_seq": 9999}
                schema, value = proposal("invented_storm", "UNOBSERVED-STORM", fake)
                return {"content": json.dumps({"decision": "continue", "guidance": "", "round_progress": [
                    {"round": self.audits, "stagnant": False, "reason": "One concrete proposal"}],
                    "state_patch": {"schema_ops": schemas + [schema], "state_ops": values + [value],
                                    "world_state": {"current_time": "year 9999"}}})}
            packet, _ = json.JSONDecoder().raw_decode(user_prompt.split("CONTEXT:\n", 1)[1])
            role, _ = json.JSONDecoder().raw_decode(user_prompt.split("ROLE:\n", 1)[1])
            assert role["role"]
            assert any(peer["node_id"] == role["node_id"] and peer["role"] == role["role"]
                       for peer in packet["communication"]["known_participants"])
            snapshots.append((role["node_id"], packet))
            text = "EARLY-FACT-18: hold dispatch until release is verified." if packet["action_index"] == 1 else f"I propose check {packet['round']} as {role['node_id']}."
            return {"content": json.dumps({"action": text, "intent": "PRIVATE-K7" if role["node_id"] == "mina" else "verify evidence",
                "turn_delta": text, "self_update": "My current work remains evidence-driven.",
                "state_updates": {"world_state": {"current_time": "year 9999"}}})}

    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider=provider).run(db, workflow, RunRequest()))
        run = db.get(RunRecord, run_id)
        assert run.status == "succeeded" and provider.audits == 24
        assert db.query(NodeRunRecord).filter_by(run_id=run_id).count() == 72
        output = json.loads(run.output_json)["simulation"]
        assert output["dynamic_state"]["values"]["release_rule"]["value"] == "EARLY-FACT-18"
        assert "invented_storm" not in output["dynamic_state"]["concepts"]
        assert output["world_state"]["current_time"] == "Monday 09:00"
        for node_id, packet in snapshots:
            state = packet["state"]["dynamic_state"]
            if packet["round"] > 1:
                assert state["values"]["release_rule"]["value"] == "EARLY-FACT-18"
                assert ("mina_secret" in state["values"]) is (node_id == "mina")
            if node_id != "mina":
                assert "PRIVATE-K7" not in json.dumps(packet)
        changes = db.query(RunEventRecord).filter_by(run_id=run_id, event="dynamic_state_updated").all()
        assert len(changes) == 24
        assert all(json.loads(event.payload_json)["rejected"] for event in changes)
        assert verify_run_audit(db, run_id).verified
    database.dispose()


@pytest.mark.parametrize("failure_stage", ["router", "agent", "audit", "budget"])
def test_continuous_failures_keep_accepted_state_actions_and_rollback_evidence(failure_stage):
    from app.service import get_run_checkpoints, build_rollback_request
    from app.schemas import RollbackRequest

    _, workflow = load_scenario("boundary_autonomy")
    class Provider:
        round = 0

        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                self.round += 1
                if self.round == 2 and failure_stage in {"router", "budget"}:
                    raise EvalBudgetExceeded("evaluation call budget exceeded") if failure_stage == "budget" else RuntimeError("router failed")
                return {"content": '{"active_node_ids":["mina","ivo","sen"]}'}
            if "continuity auditor" in system_prompt:
                if self.round == 2 and failure_stage == "audit":
                    raise RuntimeError("audit failed")
                batch, _ = json.JSONDecoder().raw_decode(user_prompt.split("UNCODED ACTIONS:\n", 1)[1])
                source = {key: batch[0][key] for key in ("node_id", "action_index", "event_seq")}
                schema, value = proposal("release_rule", "EARLY-FACT-18", source)
                private_schema, private_value = proposal("mina_secret", "PRIVATE-BUSINESS-STATE", source, "mina")
                return {"content": json.dumps({"decision": "continue", "state_patch": {
                    "schema_ops": [schema, private_schema], "state_ops": [value, private_value]}})}
            if self.round == 2 and failure_stage == "agent":
                raise RuntimeError("agent failed")
            return {"content": '{"action":"EARLY-FACT-18 remains a required constraint.","intent":"Verify the source."}'}

    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider=Provider()).run(db, workflow, RunRequest()))
        run = db.get(RunRecord, run_id)
        output = json.loads(run.output_json)
        assert run.status == "failed"
        assert len(output["actions"]) == (6 if failure_stage == "audit" else 3)
        assert output["simulation"]["dynamic_state"]["values"]["release_rule"]["value"] == "EARLY-FACT-18"
        assert output["simulation"]["dynamic_state"]["values"]["mina_secret"]["value"] == "PRIVATE-BUSINESS-STATE"
        assert output["simulation"]["dynamic_state"]["concepts"]["mina_secret"]["visibility"] == "private"
        assert output["scene_control"]["last_audited_round"] == 1
        assert json.loads(run.error_json)["type"] == ("EvalBudgetExceeded" if failure_stage == "budget" else "RuntimeError")
        final = db.query(RunEventRecord).filter_by(run_id=run_id).order_by(RunEventRecord.seq.desc()).first()
        assert final.node_id == "run_control" and final.event == "failed"
        assert verify_run_audit(db, run_id).verified
        checkpoints = get_run_checkpoints(db, run_id).checkpoints
        boundary = next(point for point in checkpoints if point.event == "episode_audited")
        _, request = build_rollback_request(db, run_id, RollbackRequest(event_seq=boundary.seq))
        snapshot = request.input["_rollback"]["continuous_snapshot"]
        assert snapshot["simulation_state"]["dynamic_state"]["values"]["mina_secret"]["value"] == "PRIVATE-BUSINESS-STATE"
    database.dispose()
