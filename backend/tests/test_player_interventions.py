from __future__ import annotations

import asyncio
import copy
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.execution.checkpoints import continuous_snapshot, restore_continuous_checkpoint
from app.execution.dynamic_state import init_dynamic_state, simulation_state_for_node
from app.execution.engine import WorkflowEngine
from app.execution.events import emit_event
from app.execution.simulation_state import init_simulation_state
from app.execution.state_coding import apply_state_batch
from app.main import app, get_db
from app.models import MemoryRecord, RunEventRecord, RunRecord, WorkflowRecord
from app.player_interventions import PlayerIntervention, drain_intervention, enqueue_intervention, intervention_status
from app.schemas import RollbackRequest, RunRequest, WorkflowDefinition
from app.run_lifecycle import fail_interrupted_run
from app.service import _build_readable_report_source, build_rollback_request, get_full_log, get_run, get_run_checkpoints, verify_run_audit


from player_fixtures import SECRET, Coder, outcome_patch, setup, workflow


def submit(db, *, version=1, instruction=SECRET, request_id="one"):
    return enqueue_intervention(db, "run", PlayerIntervention(
        instruction=instruction, base_state_version=version, request_id=request_id))


def drain(db, wf, provider, context=None):
    context = context or {"_simulation": init_simulation_state(wf)}
    asyncio.run(drain_intervention(db=db, run=db.get(RunRecord, "run"), workflow=wf, provider=provider,
                                  context=context, actions=[], round_index=1, action_index=0, simulation_seq=0))
    return context


def payloads(db, kind):
    return [json.loads(row.payload_json) for row in db.query(RunEventRecord).filter_by(event=kind).order_by(RunEventRecord.seq)]


def test_outcome_is_atomic_source_linked_private_and_rollback_exact(setup):
    db, wf, _ = setup
    baseline = {"_simulation": init_simulation_state(wf)}
    before, _ = emit_event(db, "run", "state_coder", "dynamic_state_updated", {
        "dynamic_state": baseline["_simulation"]["dynamic_state"],
        "_rollback_snapshot": continuous_snapshot(baseline, [], 0, 0, 0)})
    submit(db)
    result = drain(db, wf, Coder(), copy.deepcopy(baseline))
    dynamic = result["_simulation"]["dynamic_state"]
    assert dynamic["values"]["li_dead"]["value"] is False
    assert dynamic["version"] == 2 and dynamic["last_coded_event_seq"] == 0
    event = db.query(RunEventRecord).filter_by(event="player_outcome").one()
    assert dynamic["values"]["li_dead"]["source_refs"] == [{"event_type": "player_outcome", "event_seq": event.seq}]
    assert dynamic["values"]["li_dead"]["source_node_ids"] == []
    assert SECRET in json.dumps(simulation_state_for_node(result["_simulation"], "wang"))
    for node in ("bystander", "li"):
        view = simulation_state_for_node(result["_simulation"], node)
        assert SECRET not in json.dumps(view) and "li_dead" not in view["dynamic_state"]["concepts"]
    assert db.query(MemoryRecord).count() == 0
    assert db.query(RunEventRecord).filter_by(event="succeeded").count() == 0
    assert intervention_status(db, "run")["state_version"] == 2
    assert intervention_status(db, "run")["pending_request_id"] is None
    assert verify_run_audit(db, "run").verified
    assert SECRET in get_full_log(db, "run").content
    checkpoints = get_run_checkpoints(db, "run").checkpoints
    after = next(item.seq for item in checkpoints if item.seq > event.seq)
    for checkpoint, expected in ((before, baseline["_simulation"]), (after, result["_simulation"])):
        _, request = build_rollback_request(db, "run", RollbackRequest(event_seq=checkpoint))
        restored = {}
        restore_continuous_checkpoint(restored, request)
        assert restored["_simulation"] == expected


@pytest.mark.parametrize("failure", ["type", "owner", "subject", "visibility", "kind", "empty", "conflict", "truncated", "transport"])
def test_invalid_outcome_never_commits_partial_facts_or_raw_provider_errors(setup, failure):
    db, wf, _ = setup
    output = outcome_patch()
    if failure == "type": output["state_patch"]["state_ops"][0]["value"] = {"bad": "scalar"}
    if failure == "owner": output["state_patch"]["schema_ops"][0]["owner_id"] = "unknown"
    if failure == "subject": output["state_patch"]["schema_ops"][0]["subject_id"] = "unknown"
    if failure == "visibility": output["state_patch"]["schema_ops"][0]["visibility"] = "invalid"
    if failure == "kind": output["state_patch"]["state_ops"][0]["evidence_kind"] = "action"
    if failure == "empty": output["state_patch"] = {"schema_ops": [], "state_ops": []}
    if failure == "conflict": output["status"] = "conflict"

    class BadCoder(Coder):
        async def chat(self, **kwargs):
            response = await super().chat(**kwargs)
            if failure == "transport": raise TimeoutError("PRIVATE-TRANSPORT-KEY")
            if failure == "truncated": response["raw"] = {"choices": [{"finish_reason": "length"}]}
            return response

    submit(db)
    provider = BadCoder(output)
    result = drain(db, wf, provider)
    assert result["_simulation"]["dynamic_state"] == init_dynamic_state({"enabled": True})
    assert not payloads(db, "player_outcome") and not payloads(db, "player_applied")
    assert payloads(db, "player_rejected") and not payloads(db, "dynamic_state_updated")
    assert "PRIVATE-TRANSPORT-KEY" not in str(payloads(db, "player_rejected"))
    assert provider.calls == (1 if failure in {"conflict", "transport"} else 2)


def test_stale_version_is_rejected_at_consumption_without_a_model_call(setup):
    db, wf, _ = setup
    submit(db)
    context = {"_simulation": init_simulation_state(wf)}
    context["_simulation"]["dynamic_state"]["version"] = 2
    provider = Coder()
    drain(db, wf, provider, context)
    assert provider.calls == 0
    assert payloads(db, "player_rejected")[0]["code"] == "state_version_conflict"


def test_unclean_exit_recovery_cancels_pending_outcomes_without_replaying_them(setup):
    db, _, _ = setup
    submit(db)
    assert fail_interrupted_run(db, "run", "RUNTIME_INTERRUPTED", "Offline restart fixture.")
    assert intervention_status(db, "run")["pending_request_id"] is None
    assert payloads(db, "player_rejected")[0]["code"] == "run_ended"
    assert not payloads(db, "player_outcome") and verify_run_audit(db, "run").verified


def test_stop_during_model_request_prevents_commit(setup):
    db, wf, factory = setup
    submit(db)

    class StopCoder(Coder):
        async def chat(self, **kwargs):
            with factory() as other:
                other.get(RunRecord, "run").status = "stopping"
                other.commit()
            return await super().chat(**kwargs)

    result = drain(db, wf, StopCoder())
    assert result["_simulation"]["dynamic_state"]["version"] == 1
    assert not payloads(db, "player_outcome") and payloads(db, "player_rejected")[0]["code"] == "run_ended"


def test_event_batch_rolls_back_on_database_failure(setup, monkeypatch):
    db, wf, _ = setup
    submit(db)
    import app.player_interventions as gateway
    original = gateway.emit_event

    def fail(*args, **kwargs):
        if args[3] == "dynamic_state_updated": raise RuntimeError("write failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(gateway, "emit_event", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        drain(db, wf, Coder())
    assert not payloads(db, "player_outcome") and not payloads(db, "dynamic_state_updated")
    assert intervention_status(db, "run")["pending_request_id"] == "one"


def test_api_preconditions_idempotency_and_no_paid_request(setup):
    db, wf, factory = setup

    def get_test_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    try:
        with TestClient(app) as client:
            url = "/api/runs/run/interventions"
            body = {"mode": "outcome", "instruction": SECRET, "base_state_version": 1, "request_id": "same"}
            assert client.get("/api/runs/missing/interventions").status_code == 404
            assert client.post(url, json={**body, "base_state_version": True}).status_code == 422
            assert client.post(url, json={**body, "mode": "action"}).status_code == 422
            assert client.post(url, json={**body, "instruction": " "}).status_code == 422
            assert client.post(url, json={**body, "actor_id": "unknown"}).status_code == 422
            assert client.post(url, json={**body, "base_state_version": 2}).status_code == 409
            response = client.post(url, json=body)
            assert response.status_code == 202 and response.json()["status"] == "queued"
            assert client.post(url, json=body).json()["request_seq"] == response.json()["request_seq"]
            assert client.post(url, json={**body, "instruction": "different"}).status_code == 409
            assert client.post(url, json={**body, "request_id": "next"}).status_code == 409
            assert client.get(url).json()["pending_request_id"] == "same"
            assert db.query(RunEventRecord).filter_by(event="player_requested").count() == 1
            assert not payloads(db, "player_outcome")
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_ordinary_actor_cannot_forge_player_outcome_authority():
    patch = outcome_patch()["state_patch"]
    for operation in patch["schema_ops"] + patch["state_ops"]:
        operation["source_refs"] = [{"event_type": "player_outcome", "event_seq": 10}]
    initial = init_dynamic_state({"enabled": True})
    next_state, result = apply_state_batch(initial, patch, actions=[], seq=1, round_index=1)
    assert next_state == initial and result["rejected"]
    source = {"node_id": "wang", "action_index": 1, "event_seq": 10}
    for operation in patch["schema_ops"] + patch["state_ops"]:
        operation["source_refs"] = [source]
    next_state, result = apply_state_batch(initial, patch, actions=[source], seq=1, round_index=1)
    assert next_state == initial and any(item["reason"] == "invalid_outcome_authority" for item in result["rejected"])


def test_existing_private_ownership_cannot_be_changed_by_a_new_outcome(setup):
    db, wf, _ = setup
    submit(db)
    context = drain(db, wf, Coder())
    baseline = copy.deepcopy(context["_simulation"])
    submit(db, version=2, request_id="second")
    output = outcome_patch()
    output["state_patch"]["schema_ops"][0]["owner_id"] = "bystander"
    drain(db, wf, Coder(output), context)
    assert context["_simulation"] == baseline
    assert len(payloads(db, "player_outcome")) == 1 and payloads(db, "player_rejected")


@pytest.mark.parametrize("status", ["pending", "waiting_human", "stopping", "stopped", "failed", "succeeded"])
def test_non_running_runs_reject_outcomes_without_enqueuing(setup, status):
    db, _, _ = setup
    db.get(RunRecord, "run").status = status
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        submit(db)
    assert rejected.value.status_code == 409 and not payloads(db, "player_requested")


def test_real_engine_continues_with_owner_filtered_outcome_and_no_forged_actions(setup):
    db, wf, factory = setup

    class Provider(Coder):
        def __init__(self):
            super().__init__()
            self.actor_prompts = []

        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                with factory() as other:
                    submit(other)
                data = {"active_node_ids": ["wang", "bystander"]}
            elif "PLAYER OUTCOME STATE CODER" in system_prompt:
                assert "without witnesses" in user_prompt
                data = outcome_patch()
            elif "action consistency checker" in system_prompt:
                data = {"decision": "accept", "reason": "Offline contract fixture.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture complete.",
                        "state_patch": {"schema_ops": [], "state_ops": []}}
            else:
                self.actor_prompts.append(user_prompt)
                data = {"participation": "act", "action": "I wait by the door."}
            return {"content": json.dumps(data)}

    provider = Provider()
    asyncio.run(WorkflowEngine(provider).run(db, wf, RunRequest(), run_id="run"))
    run = db.get(RunRecord, "run")
    assert run.status == "succeeded", run.error_json
    assert len(provider.actor_prompts) == 2
    assert SECRET in provider.actor_prompts[0] and SECRET not in provider.actor_prompts[1]
    output = json.loads(run.output_json)
    assert len(output["actions"]) == 2 and all(item["node_id"] != "player" for item in output["actions"])
    assert output["simulation"]["dynamic_state"]["values"]["li_dead"]["value"] is False
    assert verify_run_audit(db, "run").verified
    assert SECRET not in " ".join(memory.content for memory in db.query(MemoryRecord))
    report_source = _build_readable_report_source(get_run(db, "run"), wf)
    assert SECRET in report_source and "private knowledge boundaries" in report_source
    before = db.query(RunEventRecord).filter_by(event="nodes_activated").one()
    _, request = build_rollback_request(db, "run", RollbackRequest(event_seq=before.seq))

    class BranchProvider(Coder):
        async def chat(self, model, system_prompt, user_prompt):
            assert SECRET not in user_prompt
            if "route attention" in system_prompt:
                data = {"active_node_ids": ["bystander"]}
            elif "action consistency checker" in system_prompt:
                data = {"decision": "accept", "reason": "Offline fixture.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Branch complete.",
                        "state_patch": {"schema_ops": [], "state_ops": []}}
            else:
                data = {"participation": "act", "action": "I remain by the door."}
            return {"content": json.dumps(data)}

    branch_id = asyncio.run(WorkflowEngine(BranchProvider()).run(db, wf, request))
    branch = db.get(RunRecord, branch_id)
    assert branch.status == "succeeded", branch.error_json
    assert json.loads(branch.output_json)["simulation"]["dynamic_state"]["values"] == {}
    assert db.query(RunEventRecord).filter_by(run_id=branch_id, event="player_outcome").count() == 0


@pytest.mark.parametrize("old_action_changes_state", [False, True])
def test_inflight_action_is_coded_before_player_outcome_and_stale_edits_are_not_overwritten(setup, old_action_changes_state):
    db, wf, factory = setup

    class Provider(Coder):
        outcome_calls = 0

        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                data = {"active_node_ids": ["wang"]}
            elif "PLAYER OUTCOME STATE CODER" in system_prompt:
                self.outcome_calls += 1
                data = outcome_patch()
            elif system_prompt == "You are the State Coder, not a participant.":
                packet, _ = json.JSONDecoder().raw_decode(user_prompt.split("CONTEXT:\n", 1)[1])
                source = packet["new_actions"][0]
                ref = {key: source[key] for key in ("node_id", "action_index", "event_seq")}
                data = {"schema_ops": [], "state_ops": []}
                if old_action_changes_state:
                    data = {"schema_ops": [{"op": "upsert", "id": "position", "description": "Wang's position.",
                        "scope": "entity", "owner_id": "wang", "subject_id": "wang", "predicate": "location",
                        "value_type": "text", "visibility": "global", "retention": {"mode": "persistent"},
                        "future_relevance": "Constrains travel.", "reason": "Accepted action.", "source_refs": [ref]}],
                        "state_ops": [{"op": "set", "concept_id": "position", "value": "door", "confidence": 1,
                            "reason": "Accepted action.", "evidence_kind": "action", "source_refs": [ref]}]}
            elif "action consistency checker" in system_prompt:
                data = {"decision": "accept", "reason": "Offline fixture.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture complete.",
                        "state_patch": {"schema_ops": [], "state_ops": []}}
            else:
                with factory() as other:
                    submit(other)
                data = {"participation": "act", "action": "I reach the door."}
            return {"content": json.dumps(data)}

    provider = Provider()
    asyncio.run(WorkflowEngine(provider).run(db, wf, RunRequest(), run_id="run"))
    run = db.get(RunRecord, "run")
    assert run.status == "succeeded", run.error_json
    output = json.loads(run.output_json)
    assert len(output["actions"]) == 1
    assert output["simulation"]["dynamic_state"]["last_coded_event_seq"] == output["actions"][0]["event_seq"]
    assert provider.outcome_calls == (0 if old_action_changes_state else 1)
    if old_action_changes_state:
        assert payloads(db, "player_rejected")[0]["code"] == "state_version_conflict"
        assert "li_dead" not in output["simulation"]["dynamic_state"]["values"]
    else:
        assert payloads(db, "player_applied") and output["simulation"]["dynamic_state"]["values"]["li_dead"]["value"] is False
    assert verify_run_audit(db, "run").verified
