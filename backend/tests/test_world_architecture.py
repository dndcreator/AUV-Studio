import asyncio
import copy
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.dynamic_state import init_dynamic_state, compact_dynamic_state
from app.execution.engine import WorkflowEngine
from app.execution.memory import load_recent_memories
from app.execution.simulation_state import init_simulation_state
from app.execution.state_coding import apply_state_batch
from app.execution.world_entities import entity_summary
from app.models import MemoryRecord, RunRecord, RunDefinitionRecord
from app.schemas import RollbackRequest, RunRequest, WorkflowNode
from app.service import build_rollback_request, create_workflow, get_events, request_run_stop, verify_run_audit
from app.stored_world import read_world, resume_checkpoint_seq
from test_full_batch import Provider
from app.evaluation.parallel_trial import workflow


class SerialProvider(Provider):
    async def chat(self, **kwargs):
        if "action consistency checker" in kwargs["system_prompt"] and "BATCH ADMISSION" not in kwargs["system_prompt"]:
            return {"content": json.dumps({"decision": "accept", "reason": "Offline fixture.",
                "scope": {"self_owned": True, "public_only": True}, "findings": []})}
        return await super().chat(**kwargs)


def test_entities_include_nonagents_and_groups_are_not_population_counts():
    wf = workflow("castle")
    wf.nodes[1].config.update(entity_type="group", entity_name="Guild")
    wf.environment.simulation["dynamic_state"]["entities"] = {
        "quiet_npc": {"kind": "individual", "name": "No executable node"},
        "hidden": {"kind": "artifact", "visibility": "private", "owner_id": "a"}}
    state = init_simulation_state(wf)["dynamic_state"]
    summary = entity_summary(state, include_private=True)
    assert summary["count"] == 4 and summary["by_kind"]["individual"] == 2 and summary["by_kind"]["group"] == 1
    assert summary["entities"]["quiet_npc"]["agent_node_ids"] == []
    assert "hidden" not in compact_dynamic_state(state, node_id="b")["entities"]
    assert "hidden" in compact_dynamic_state(state, node_id="a")["entities"]


def test_entity_registration_and_attribute_batch_is_atomic_and_source_bound():
    source = {"node_id": "a", "event_seq": 4, "action_index": 1}
    operation = {"op": "register", "id": "key", "name": "Key", "kind": "artifact", "source_refs": [source]}
    state, result = apply_state_batch(init_dynamic_state({"enabled": True}),
        {"schema_ops": [], "state_ops": [], "entity_ops": [operation]}, actions=[source], seq=1, round_index=1)
    assert result["changed"] and entity_summary(state)["count"] == 1
    assert state["entities"]["key"]["source"]["source_refs"] == [source]
    broken = {**operation, "id": "forged", "source_refs": [{**source, "event_seq": 999}]}
    after, result = apply_state_batch(state, {"schema_ops": [], "state_ops": [], "entity_ops": [broken]},
        actions=[source], seq=2, round_index=2)
    assert after == state and result["coding"]["status"] == "pending"
    forged = {**operation, "id": "forged", "agent_node_ids": ["a"]}
    after, result = apply_state_batch(state, {"schema_ops": [], "state_ops": [], "entity_ops": [forged]},
        actions=[source], seq=2, round_index=2)
    assert after == state and result["rejected"]


@pytest.mark.parametrize("target,private,valid", [("holder", False, True), ("missing", False, False), ("secret", False, False)])
def test_relations_reject_dangling_and_private_target_leaks(target, private, valid):
    from test_state_coding import action, patch
    state = init_dynamic_state({"enabled": True})
    state["entities"] = {"holder": {"id": "holder", "kind": "individual", "visibility": "global", "status": "active"},
                         "secret": {"id": "secret", "kind": "individual", "visibility": "private", "owner_id": "b", "status": "active"}}
    proposal = patch(action(), subject="key", predicate="holder", value=target)
    proposal["schema_ops"][0]["value_type"] = "entity_ref"
    updated, result = apply_state_batch(state, proposal, actions=[action()], seq=1, round_index=1)
    assert (result["coding"]["status"] == "coded") == valid
    if not valid:
        assert updated == state


def test_memory_ancestry_excludes_siblings_future_and_unrelated_workflow_runs(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "branches.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        wf = workflow("castle"); create_workflow(db, wf)
        for rid, parent, cutoff in (("root", None, None), ("left", "root", 10), ("right", "root", 10), ("fresh", None, None), ("grandchild", "left", 40)):
            db.add(RunRecord(id=rid, workflow_id=wf.id, status="stopped", retry_from_run_id=parent,
                input_json=json.dumps({"_rollback": {"event_seq": cutoff}}) if cutoff else "{}"))
        db.commit()
        for rid, text, seq in (("root", "past", 5), ("root", "future", 15), ("left", "left memory", 30), ("right", "right memory", 35), ("fresh", "other world", 3)):
            db.add(MemoryRecord(workflow_id=wf.id, run_id=rid, node_id="a", role="custom", content=text,
                tags_json=json.dumps({"kind": "fact", "source_event_seq": seq})))
        db.commit()
    with sessions() as db:
        assert [row["content"] for row in load_recent_memories(db, wf.id, run_id="left")] == ["past", "left memory"]
        assert [row["content"] for row in load_recent_memories(db, wf.id, run_id="right")] == ["past", "right memory"]
        assert [row["content"] for row in load_recent_memories(db, wf.id, run_id="grandchild")] == ["past", "left memory"]
        assert [row["content"] for row in load_recent_memories(db, wf.id, run_id="fresh")] == ["other world"]
    engine.dispose()


def test_pause_read_resume_and_fork_restore_pinned_world_without_repeating_accepted_action(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "saved.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    wf = workflow("castle"); wf.environment.simulation.update(horizon_rounds=2)
    wf.nodes.append(WorkflowNode(id="director_1", type="director", position={"x": 0, "y": 0}))
    with sessions() as db:
        create_workflow(db, wf)
        class StopAfterFirst(SerialProvider):
            async def chat(self, **kwargs):
                if "autonomous participant" in kwargs["user_prompt"] and not self.inputs:
                    request_run_stop(db, "source")
                return await super().chat(**kwargs)
        asyncio.run(WorkflowEngine(StopAfterFirst()).run(db, wf, RunRequest(), run_id="source"))
        assert db.get(RunRecord, "source").status == "stopped"
        seq = resume_checkpoint_seq(db, "source")
        source_world = read_world(db, "source")
        _, request = build_rollback_request(db, "source", RollbackRequest(event_seq=seq))
        from app.main import app, get_db
        app.dependency_overrides[get_db] = lambda: db
        enqueued = []
        monkeypatch.setattr("app.main._enqueue_or_429", lambda **kwargs: enqueued.append(kwargs))
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                assert client.get("/api/runs/source/world-state").json()["checkpoint_seq"] == seq
                response = client.post("/api/runs/source/resume")
                assert response.status_code == 200, response.text
                assert len(enqueued) == 1 and enqueued[0]["request"].retry_from_run_id == "source"
                assert enqueued[0]["workflow"].version == wf.version
        finally:
            app.dependency_overrides.clear()
    # New database sessions model application exit/re-entry, not in-memory context reuse.
    for branch in ("left", "right"):
        with sessions() as db:
            edited = wf.model_copy(deep=True)
            edited.version = 99
            edited.environment.scenario = "INCOMPATIBLE EDITOR WORLD"
            provider = SerialProvider()
            asyncio.run(WorkflowEngine(provider).run(db, edited, copy.deepcopy(request), run_id=branch))
            run = db.get(RunRecord, branch)
            assert run.status != "failed", run.error_json
            own = [event for event in get_events(db, branch, after_seq=0, limit=10000).events if event.event == "succeeded" and "action" in event.payload.get("output", {})]
            assert own[0].node_id == "b"  # A's accepted action is not replayed.
            assert all(event.payload["input"]["context"]["round"] != 1 or event.node_id != "a" for event in own)
            assert "INCOMPATIBLE EDITOR WORLD" not in db.get(RunDefinitionRecord, branch).definition_json
            assert read_world(db, branch)["workflow_version"] == source_world["workflow_version"]
            assert verify_run_audit(db, branch).verified
            recalled = load_recent_memories(db, wf.id, run_id=branch)
            assert not {row["run_id"] for row in recalled} & ({"left", "right"} - {branch})
            if branch == "left":
                # A later same-run human response must not erase the original branch cutoff.
                run.input_json = json.dumps({"_human_response": {"response": "continue"}})
                db.commit()
                assert {row["id"] for row in load_recent_memories(db, wf.id, run_id=branch)} == {row["id"] for row in recalled}
    with sessions() as db:
        saved = db.get(RunDefinitionRecord, "source")
        modified = json.loads(saved.definition_json)
        modified["environment"]["scenario"] = "TAMPERED"
        saved.definition_json = json.dumps(modified)
        db.commit()
        with pytest.raises(HTTPException) as error:
            read_world(db, "source")
        assert error.value.status_code == 409
    engine.dispose()
