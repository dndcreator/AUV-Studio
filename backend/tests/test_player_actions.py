import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.execution.engine import WorkflowEngine
from app.main import app, get_db
from app.models import MemoryRecord, RunEventRecord, RunRecord, WorkflowRecord
from app.player_controls import ControlRequest, request_control
from app.player_history import intervention_history
from app.player_interventions import PlayerIntervention, enqueue_intervention, intervention_status
from app.schemas import RollbackRequest, RunRequest
from app.service import build_rollback_request, request_run_stop, verify_run_audit
from player_fixtures import Coder, outcome_patch, setup

ACCEPT = {"decision": "accept", "reason": "Offline fixture.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
EMPTY = {"schema_ops": [], "state_ops": []}


def send_action(db):
    return enqueue_intervention(db, "run", PlayerIntervention(mode="action", actor_id="wang", instruction="Try the door.",
        request_id="action", base_state_version=intervention_status(db, "run")["state_version"]))


def events(db, name):
    db.expire_all()
    return db.query(RunEventRecord).filter_by(run_id="run", event=name).order_by(RunEventRecord.seq).all()


async def until(factory, name):
    for _ in range(150):
        with factory() as db:
            if events(db, name): return
        await asyncio.sleep(.02)
    raise AssertionError(f"No {name} within 3 seconds")


@pytest.mark.parametrize("accepted", [True, False])
def test_one_shot_action_obeys_role_checker_and_never_forces_success_or_fails_run(setup, accepted):
    db, wf, factory = setup
    # Make the peer act first so the before-checkpoint must retain real prior work.
    wf.nodes = [wf.nodes[0], wf.nodes[3], wf.nodes[1], wf.nodes[2]]
    db.get(WorkflowRecord, wf.id).definition_json = wf.model_dump_json(); db.commit()
    calls = []

    class Provider:
        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                with factory() as other: send_action(other)
                data = {"active_node_ids": ["bystander"]}
            elif "action consistency checker" in system_prompt:
                packet = json.loads(user_prompt)
                if packet["CANDIDATE"]["actor_id"] == "wang":
                    row = next(item for item in packet["EVIDENCE"] if item["id"] == "actor.input")
                    assert row["value"]["player_request"]["instruction"] == "Try the door."
                data = ACCEPT if accepted or packet["CANDIDATE"]["actor_id"] != "wang" else {
                    **ACCEPT, "decision": "conflict", "findings": [{"category": "capability", "reason": "Unsupported effect.",
                    "evidence_ids": [packet["EVIDENCE"][0]["id"]]}]}
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture.", "state_patch": EMPTY}
            else:
                calls.append(user_prompt)
                if "PLAYER ACTION REQUEST" in user_prompt:
                    assert "may refuse or wait" in user_prompt
                    data = {"participation": "wait", "action": "I decline."}
                else:
                    assert "Try the door" not in user_prompt
                    data = {"participation": "act", "action": "I watch the wall."}
            return {"content": json.dumps(data)}

    asyncio.run(WorkflowEngine(Provider()).run(db, wf, RunRequest(), run_id="run"))
    run = db.get(RunRecord, "run")
    assert run.status == "succeeded", run.error_json
    assert len(json.loads(run.output_json)["actions"]) == (2 if accepted else 1)
    assert len(calls) == 2
    history = intervention_history(db, "run")["items"][0]
    assert history["status"] == ("applied" if accepted else "rejected")
    _, before = build_rollback_request(db, "run", RollbackRequest(event_seq=history["before_checkpoint_seq"]))
    assert len(before.input["_rollback"]["continuous_snapshot"]["actions"]) == 1
    if accepted: assert history["after_checkpoint_seq"]
    else: assert not any("decline" in row.content for row in db.query(MemoryRecord))
    assert verify_run_audit(db, "run").verified


def test_takeover_waits_without_calls_then_player_action_and_handoff_restore_autonomy(setup):
    db, wf, factory = setup
    wf.environment.simulation["horizon_rounds"] = 2
    db.get(WorkflowRecord, wf.id).definition_json = wf.model_dump_json(); db.commit()

    class Provider:
        rounds = 0; controlled = 0; autonomous = 0; checks = 0
        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                self.rounds += 1
                if self.rounds == 1:
                    with factory() as other: request_control(other, "run", ControlRequest(actor_id="wang", control="player_controlled"))
                data = {"active_node_ids": ["wang"]}
            elif "player-controlled actor" in system_prompt:
                self.controlled += 1
                assert "Try the door" in user_prompt and "do not replace it" in user_prompt.lower()
                with factory() as other:
                    request_control(other, "run", ControlRequest(actor_id="wang", control="autonomous"))
                    assert intervention_status(other, "run")["handoffs"] == {"wang": "autonomous"}
                data = {"participation": "act", "action": "I test the door handle."}
            elif "action consistency checker" in system_prompt:
                self.checks += 1; data = ACCEPT
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop" if self.rounds == 2 else "continue", "task_complete": self.rounds == 2,
                    "completion_reason": "Fixture.", "state_patch": EMPTY}
            else:
                self.autonomous += 1
                assert "PLAYER ACTION REQUEST" not in user_prompt and "I test the door handle" in user_prompt
                data = {"participation": "act", "action": "I step away."}
            return {"content": json.dumps(data)}

    provider = Provider()
    async def operator():
        await until(factory, "player_waiting")
        await asyncio.sleep(.05)
        assert (provider.rounds, provider.controlled, provider.autonomous) == (1, 0, 0)
        with factory() as other:
            assert intervention_status(other, "run")["waiting_actor_ids"] == ["wang"]
            send_action(other)
    async def scenario():
        await asyncio.wait_for(asyncio.gather(WorkflowEngine(provider).run(db, wf, RunRequest(), run_id="run"), operator()), 8)
    asyncio.run(scenario())
    assert db.get(RunRecord, "run").status == "succeeded"
    assert (provider.controlled, provider.autonomous, provider.checks) == (1, 1, 2)
    changed, accepted = events(db, "player_control_changed"), events(db, "player_applied")[0]
    assert changed[0].seq < accepted.seq < changed[1].seq
    assert intervention_status(db, "run")["controls"] == {"wang": "autonomous"}
    assert not intervention_status(db, "run")["waiting_actor_ids"]
    _, branch = build_rollback_request(db, "run", RollbackRequest(event_seq=accepted.seq))
    snapshot = branch.input["_rollback"]["continuous_snapshot"]
    assert snapshot["player_controls"] == {"wang": "player_controlled"} and len(snapshot["actions"]) == 1
    release = next(row for row in intervention_history(db, "run")["items"] if row["mode"] == "control" and row["control"] == "autonomous")
    _, before_release = build_rollback_request(db, "run", RollbackRequest(event_seq=release["before_checkpoint_seq"]))
    previous = before_release.input["_rollback"]["continuous_snapshot"]
    assert previous["player_controls"] == {"wang": "player_controlled"} and len(previous["actions"]) == 1
    assert verify_run_audit(db, "run").verified


def test_stop_while_waiting_for_player_does_not_generate_an_action(setup):
    db, wf, factory = setup
    calls = []
    class Provider:
        async def chat(self, **kwargs):
            calls.append(kwargs)
            assert "route attention" in kwargs["system_prompt"]
            with factory() as other: request_control(other, "run", ControlRequest(actor_id="wang", control="player_controlled"))
            return {"content": '{"active_node_ids":["wang"]}'}
    async def stop():
        await until(factory, "player_waiting")
        with factory() as other: request_run_stop(other, "run")
    async def scenario():
        await asyncio.wait_for(asyncio.gather(WorkflowEngine(Provider()).run(db, wf, RunRequest(), run_id="run"), stop()), 8)
    asyncio.run(scenario())
    assert db.get(RunRecord, "run").status == "stopped" and len(calls) == 1
    assert not events(db, "succeeded") and not intervention_status(db, "run")["waiting_actor_ids"]


def test_other_selected_autonomous_actor_runs_before_a_controlled_actor_waits(setup):
    db, wf, factory = setup
    acted = []
    class Provider:
        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                with factory() as other: request_control(other, "run", ControlRequest(actor_id="wang", control="player_controlled"))
                data = {"active_node_ids": ["wang", "bystander"]}
            elif "action consistency checker" in system_prompt: data = ACCEPT
            else:
                assert "bystander" in user_prompt
                acted.append("bystander"); data = {"participation": "act", "action": "I watch the corridor."}
            return {"content": json.dumps(data)}
    async def stop():
        await until(factory, "player_waiting")
        assert acted == ["bystander"]
        with factory() as other: request_run_stop(other, "run")
    async def scenario():
        await asyncio.wait_for(asyncio.gather(WorkflowEngine(Provider()).run(db, wf, RunRequest(), run_id="run"), stop()), 8)
    asyncio.run(scenario())
    assert db.get(RunRecord, "run").status == "stopped"


def test_control_api_requires_handoff_and_preserves_idempotent_history(setup):
    db, _, factory = setup
    def dependency():
        with factory() as session: yield session
    app.dependency_overrides[get_db] = dependency
    try:
        client = TestClient(app); url = "/api/runs/run/controls"
        body = {"actor_id": "wang", "control": "player_controlled", "request_id": "take"}
        assert client.post(url, json={**body, "actor_id": "ghost"}).status_code == 422
        assert client.post(url, json=body).status_code == 202
        assert client.post(url, json=body).json()["status"] == "recorded"
        assert client.post(url, json={**body, "control": "autonomous"}).status_code == 409
        assert client.get("/api/runs/run/interventions").json()["handoffs"] == {"wang": "player_controlled"}
        assert client.post("/api/runs/run/interventions", json={"mode": "action", "actor_id": "wang",
            "instruction": "Try it", "base_state_version": 1}).status_code == 409
        page = client.get("/api/runs/run/interventions/history?limit=1").json()
        assert page["items"][0]["mode"] == "control" and page["items"][0]["status"] == "queued"
        assert client.post(url, json={**body, "actor_id": "li", "request_id": "take-li"}).status_code == 202
        first = client.get("/api/runs/run/interventions/history?limit=1").json()
        second = client.get(f"/api/runs/run/interventions/history?limit=1&before_seq={first['next_before_seq']}").json()
        assert first["items"][0]["actor_id"] == "li" and second["items"][0]["actor_id"] == "wang"
        assert second["next_before_seq"] is None
        request_run_stop(db, "run")
        from app.player_interventions import reject_pending_intervention
        reject_pending_intervention(db, "run")
        assert intervention_history(db, "run")["items"][0]["status"] == "rejected"
    finally: app.dependency_overrides.pop(get_db, None)


def test_inflight_autonomous_turn_finishes_before_handoff_and_no_second_owner_runs(setup):
    db, wf, factory = setup
    class Provider:
        calls = 0
        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt: data = {"active_node_ids": ["wang"]}
            elif "action consistency checker" in system_prompt: data = ACCEPT
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture.", "state_patch": EMPTY}
            else:
                self.calls += 1
                with factory() as other:
                    request_control(other, "run", ControlRequest(actor_id="wang", control="player_controlled"))
                    state = intervention_status(other, "run")
                    assert state["handoffs"] == {"wang": "player_controlled"} and not state["controls"]
                data = {"participation": "act", "action": "I finish my current step."}
            return {"content": json.dumps(data)}
    provider = Provider()
    asyncio.run(WorkflowEngine(provider).run(db, wf, RunRequest(), run_id="run"))
    assert db.get(RunRecord, "run").status == "succeeded" and provider.calls == 1
    assert events(db, "succeeded")[0].seq < events(db, "player_control_changed")[0].seq
    assert verify_run_audit(db, "run").verified


def test_controlled_external_agent_is_not_invoked_for_player_generation(setup):
    db, wf, factory = setup
    wf.nodes[1].type = "external_agent"
    wf.nodes[1].config = {"integration_mode": "http", "endpoint_url": "http://must-not-be-called.invalid"}
    db.get(WorkflowRecord, wf.id).definition_json = wf.model_dump_json(); db.commit()
    class Provider:
        controlled = 0
        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                with factory() as other: request_control(other, "run", ControlRequest(actor_id="wang", control="player_controlled"))
                data = {"active_node_ids": ["wang"]}
            elif "player-controlled actor" in system_prompt:
                self.controlled += 1; data = {"participation": "act", "action": "I test the handle."}
            elif "action consistency checker" in system_prompt: data = ACCEPT
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture.", "state_patch": EMPTY}
            else: raise AssertionError("External/autonomous generation must not run under player control")
            return {"content": json.dumps(data)}
    provider = Provider()
    async def operator():
        await until(factory, "player_waiting")
        with factory() as other: send_action(other)
    async def scenario():
        await asyncio.wait_for(asyncio.gather(WorkflowEngine(provider).run(db, wf, RunRequest(), run_id="run"), operator()), 8)
    asyncio.run(scenario())
    assert db.get(RunRecord, "run").status == "succeeded" and provider.controlled == 1


@pytest.mark.parametrize("tampered", [False, True])
def test_branch_publishes_verified_public_state_and_rejects_changed_source_snapshot(setup, tampered):
    from app.player_interventions import drain_intervention
    from app.execution.simulation_state import init_simulation_state
    db, wf, _ = setup
    patch = outcome_patch()
    patch["state_patch"]["schema_ops"] = patch["state_patch"]["schema_ops"][:1]
    patch["state_patch"]["state_ops"] = patch["state_patch"]["state_ops"][:1]
    patch["state_patch"]["schema_ops"][0].update(id="position", description="Wang's public location.", subject_id="wang",
        predicate="location", visibility="global", value_type="text")
    patch["state_patch"]["state_ops"][0].update(concept_id="position", value="hall")
    enqueue_intervention(db, "run", PlayerIntervention(instruction="Wang is in the hall.", base_state_version=1))
    asyncio.run(drain_intervention(db=db, run=db.get(RunRecord, "run"), workflow=wf, provider=Coder(patch),
        context={"_simulation": init_simulation_state(wf)}, actions=[], round_index=1, action_index=0, simulation_seq=0))
    accepted = events(db, "player_applied")[0]
    _, request = build_rollback_request(db, "run", RollbackRequest(event_seq=accepted.seq))
    if tampered:
        request.input["_rollback"]["continuous_snapshot"]["simulation_state"]["dynamic_state"]["values"]["position"]["value"] = "forged"
    class Provider:
        calls = 0
        async def chat(self, model, system_prompt, user_prompt):
            self.calls += 1
            if "route attention" in system_prompt: data = {"active_node_ids": ["wang"]}
            elif "action consistency checker" in system_prompt: data = ACCEPT
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture.", "state_patch": EMPTY}
            else: data = {"participation": "wait", "action": "I wait in the hall."}
            return {"content": json.dumps(data)}
    provider = Provider()
    branch_id = asyncio.run(WorkflowEngine(provider).run(db, wf, request))
    branch = db.get(RunRecord, branch_id)
    anchors = db.query(RunEventRecord).filter_by(run_id=branch_id, event="state_restored").all()
    if tampered:
        assert branch.status == "failed" and not anchors and provider.calls == 0
    else:
        assert branch.status == "succeeded", branch.error_json
        data = json.loads(anchors[0].payload_json)
        assert data["dynamic_state"]["values"]["position"]["value"] == "hall"
        assert len(data["source_event_hash"]) == 64 and data["checkpoint_seq"] == accepted.seq
        assert verify_run_audit(db, branch_id).verified
