from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.dynamic_state import (
    apply_dynamic_state_proposal,
    compact_dynamic_state,
    expire_dynamic_state,
    init_dynamic_state,
    parse_state_coder_output,
    simulation_state_for_node,
)
from app.execution.engine import WorkflowEngine, WorkflowValidationError
from app.execution.simulation_state import init_simulation_state
from app.models import RunEventRecord, RunRecord, WorkflowRecord
from app.schemas import EnvironmentSpec, NodePosition, RollbackRequest, RunEvent, RunRequest, WorkflowDefinition, WorkflowNode
from app.service import build_rollback_request, get_events


class SequenceProvider:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.index = 0

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        output = self.outputs[min(self.index, len(self.outputs) - 1)]
        self.index += 1
        return {"content": output, "raw": {"ok": True}}


def _source(node_id: str = "agent_1", action_index: int = 1, event_seq: int = 10) -> dict:
    return {"node_id": node_id, "action_index": action_index, "event_seq": event_seq}


def _proposal(
    concept_id: str,
    *,
    visibility: str = "global",
    owner_id: str = "",
    value_type: str = "text",
    value: object = "active",
    source: dict | None = None,
    retention: dict | None = None,
) -> dict:
    source_ref = source or _source(owner_id or "agent_1")
    return {
        "schema_ops": [
            {
                "op": "upsert",
                "id": concept_id,
                "description": "A newly discovered condition that constrains a later decision.",
                "scope": "entity" if owner_id else "global",
                "owner_id": owner_id,
                "value_type": value_type,
                "visibility": visibility,
                "retention": retention or {"mode": "until_resolved"},
                "future_relevance": "Forgetting it would change which later action is reasonable.",
                "reason": "The latest action created a durable unresolved consequence.",
                "source_refs": [source_ref],
            }
        ],
        "state_ops": [
            {
                "op": "set",
                "concept_id": concept_id,
                "value": value,
                "confidence": 0.82,
                "reason": "Explicitly established by the action.",
                "source_refs": [source_ref],
            }
        ],
    }


def test_state_coder_discovers_arbitrary_concepts_without_domain_schema() -> None:
    state = init_dynamic_state({"enabled": True})
    updated, result = apply_dynamic_state_proposal(
        state,
        _proposal("unpublished_methodological_risk"),
        seq=4,
        source_actions=[{"node_id": "agent_1", "action_index": 1, "event_seq": 10}],
        round_index=1,
    )

    assert result["changed"] is True
    assert set(updated["concepts"]) == {"unpublished_methodological_risk"}
    assert updated["values"]["unpublished_methodological_risk"]["value"] == "active"
    assert updated["history"][0]["source_refs"] == [_source()]


def test_state_coder_enforces_relevance_and_growth_budgets() -> None:
    state = init_dynamic_state({"enabled": True, "max_new_per_round": 1})
    proposal = _proposal("first_constraint")
    proposal["schema_ops"].append({**proposal["schema_ops"][0], "id": "second_constraint"})
    proposal["state_ops"].append({**proposal["state_ops"][0], "concept_id": "second_constraint"})
    proposal["schema_ops"].append(
        {
            "op": "upsert",
            "id": "transient_detail",
            "description": "A gesture.",
            "future_relevance": "",
            "reason": "",
            "source_refs": [_source()],
        }
    )

    updated, result = apply_dynamic_state_proposal(
        state,
        proposal,
        seq=2,
        source_actions=[{"node_id": "agent_1", "action_index": 1, "event_seq": 10}],
        round_index=1,
    )

    assert set(updated["concepts"]) == {"first_constraint"}
    assert {item["reason"] for item in result["rejected"]} >= {"round_budget", "missing_relevance_evidence", "unknown_concept"}


def test_private_dynamic_concepts_are_visible_only_to_their_owner() -> None:
    state = init_dynamic_state({"enabled": True})
    updated, _ = apply_dynamic_state_proposal(
        state,
        _proposal("concealed_intention", visibility="private", owner_id="agent_1"),
        seq=3,
        source_actions=[{"node_id": "agent_1", "action_index": 1, "event_seq": 10}],
        round_index=1,
    )
    simulation = {"mode": "roleplay", "dynamic_state": updated}

    owner_view = simulation_state_for_node(simulation, "agent_1")["dynamic_state"]
    peer_view = simulation_state_for_node(simulation, "agent_2")["dynamic_state"]
    public_view = compact_dynamic_state(updated)

    assert "concealed_intention" in owner_view["concepts"]
    assert "concealed_intention" not in peer_view["concepts"]
    assert "concealed_intention" not in public_view["concepts"]


def test_state_value_type_is_enforced() -> None:
    state = init_dynamic_state({"enabled": True})
    proposal = _proposal("numeric_risk", value_type="scalar", value={"wrong": True})
    updated, result = apply_dynamic_state_proposal(
        state,
        proposal,
        seq=4,
        source_actions=[_source()],
        round_index=1,
    )

    assert "numeric_risk" not in updated["concepts"]
    assert "numeric_risk" not in updated["values"]
    assert any(item["reason"] == "value_type_mismatch" for item in result["rejected"])
    assert any(item["reason"] == "new_concept_without_value" for item in result["rejected"])


def test_private_owner_and_schema_identity_are_protected() -> None:
    state = init_dynamic_state({"enabled": True})
    state, _ = apply_dynamic_state_proposal(
        state,
        _proposal("private_plan", visibility="private", owner_id="agent_1"),
        seq=4,
        source_actions=[_source()],
        round_index=1,
    )
    hostile = _proposal(
        "private_plan",
        visibility="global",
        owner_id="agent_2",
        source=_source("agent_2", 2, 20),
    )
    updated, result = apply_dynamic_state_proposal(
        state,
        hostile,
        seq=5,
        source_actions=[_source("agent_2", 2, 20)],
        round_index=2,
    )

    assert updated["concepts"]["private_plan"]["owner_id"] == "agent_1"
    assert updated["concepts"]["private_plan"]["visibility"] == "private"
    assert any(item["reason"].startswith("immutable_fields:") for item in result["rejected"])
    assert any(item["reason"] == "private_owner_not_source" for item in result["rejected"])


def test_state_patch_requires_exact_action_reference() -> None:
    state = init_dynamic_state({"enabled": True})
    updated, result = apply_dynamic_state_proposal(
        state,
        _proposal("unsupported_fact", source=_source("agent_1", 1, 999)),
        seq=4,
        source_actions=[_source("agent_1", 1, 10)],
        round_index=1,
    )

    assert not updated["concepts"]
    assert "missing_or_invalid_source_ref" in {item["reason"] for item in result["rejected"]}


def test_round_retention_expires_automatically() -> None:
    state = init_dynamic_state({"enabled": True})
    state, _ = apply_dynamic_state_proposal(
        state,
        _proposal("temporary_blocker", retention={"mode": "rounds", "ttl_rounds": 2}),
        seq=4,
        source_actions=[_source()],
        round_index=1,
    )
    state, early = expire_dynamic_state(state, round_index=2, seq=5)
    state, expired = expire_dynamic_state(state, round_index=3, seq=6)

    assert early["changed"] is False
    assert expired["expired_concepts"] == ["temporary_blocker"]
    assert state["concepts"]["temporary_blocker"]["status"] == "retired"
    assert "temporary_blocker" not in state["values"]


def test_continuous_dynamic_state_requires_director() -> None:
    workflow = WorkflowDefinition(
        id="wf_no_director",
        name="invalid",
        nodes=[WorkflowNode(id="agent_1", type="agent", position=NodePosition(x=0, y=0), config={}, inputs={})],
        edges=[],
        entry_nodes=["agent_1"],
        environment=EnvironmentSpec(simulation={"execution_model": "continuous"}),
    )

    try:
        WorkflowEngine._validate(workflow)
    except WorkflowValidationError as exc:
        assert "requires a Director" in str(exc)
    else:
        raise AssertionError("continuous dynamic state accepted a workflow without Director")


def test_dynamic_state_checkpoint_restores_exact_private_snapshot() -> None:
    database = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=database)
    db = sessionmaker(bind=database)()
    snapshot = {
        "simulation_state": {
            "mode": "roleplay",
            "dynamic_state": {
                "enabled": True,
                "version": 7,
                "schema_version": 3,
                "concepts": {"private_plan": {"status": "active", "visibility": "private", "owner_id": "mei"}},
                "values": {"private_plan": {"value": "leave quietly"}},
                "history": [],
                "limits": {},
            },
        },
        "actions": [{"node_id": "mei", "action_index": 2, "event_seq": 9}],
        "round": 2,
        "action_index": 2,
        "simulation_seq": 2,
    }
    db.add(WorkflowRecord(id="wf", name="wf", version=1, definition_json="{}"))
    db.add(RunRecord(id="source", workflow_id="wf", status="succeeded", input_json="{}", output_json="{}", error_json="null"))
    db.commit()
    event = RunEventRecord(
        run_id="source",
        node_id="state_coder",
        event="dynamic_state_updated",
        payload_json=json.dumps({"changed": True, "_rollback_snapshot": snapshot}),
    )
    db.add(event)
    db.commit()

    _, request = build_rollback_request(db, "source", RollbackRequest(event_seq=int(event.seq)))
    context = {"_simulation": {"dynamic_state": {"version": 1}}}
    WorkflowEngine._restore_continuous_checkpoint(context, request)
    public_event = get_events(db, "source", after_seq=0, limit=10).events[0]

    assert context["_simulation"] == snapshot["simulation_state"]
    assert context["_continuous_resume"]["actions"] == snapshot["actions"]
    assert "_rollback_snapshot" not in public_event.payload


def test_rollback_memory_view_excludes_future_source_run_memories() -> None:
    request = RunRequest(
        input={"_rollback": {"from_run_id": "source", "event_seq": 10}},
        retry_from_run_id="source",
        retry_from_node="state_coder",
    )
    memories = [
        {"run_id": "older", "source_event_seq": 99, "content": "prior-run memory"},
        {"run_id": "source", "source_event_seq": 8, "content": "before checkpoint"},
        {"run_id": "source", "source_event_seq": 12, "content": "future leak"},
        {"run_id": "source", "source_event_seq": None, "content": "untraceable source-run memory"},
    ]

    filtered = WorkflowEngine._memories_at_rollback(memories, request)

    assert [item["content"] for item in filtered] == ["prior-run memory", "before checkpoint"]


def test_invalid_state_coder_json_degrades_to_empty_patch() -> None:
    assert parse_state_coder_output("not-json")["parse_error"] == "invalid_json"


def test_dynamic_state_events_are_part_of_the_public_protocol() -> None:
    event = RunEvent(
        seq=1,
        run_id="run_1",
        node_id="state_coder",
        event="dynamic_state_updated",
        timestamp="2026-10-04T00:00:00Z",
        payload={"changed": False},
    )
    assert event.event == "dynamic_state_updated"


def test_existing_continuous_workflow_enables_dynamic_state_without_migration() -> None:
    workflow = WorkflowDefinition(
        id="wf_existing_continuous",
        name="existing continuous",
        nodes=[],
        edges=[],
        entry_nodes=[],
        environment=EnvironmentSpec(simulation={"execution_model": "continuous"}),
    )
    assert init_simulation_state(workflow)["dynamic_state"]["enabled"] is True


def test_continuous_engine_applies_state_patch_inside_existing_director_audit() -> None:
    database = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=database)
    db = sessionmaker(bind=database)()
    workflow = WorkflowDefinition(
        id="wf_dynamic_state",
        name="dynamic state",
        nodes=[
            WorkflowNode(id="director_1", type="director", position=NodePosition(x=0, y=0), config={}, inputs={}),
            WorkflowNode(
                id="agent_1",
                type="agent",
                position=NodePosition(x=100, y=0),
                config={"entity_name": "Analyst", "entity_type": "individual"},
                inputs={"prompt": "act"},
            ),
        ],
        edges=[],
        entry_nodes=["director_1"],
        environment=EnvironmentSpec(
            simulation={
                "mode": "research",
                "execution_model": "continuous",
                "horizon_rounds": 1,
                "director_interval_rounds": 1,
                "dynamic_state": {"enabled": True},
            }
        ),
    )
    state_patch = _proposal("unresolved_sample_bias", source=_source("agent_1", 1, 4))
    provider = SequenceProvider(
        [
            '{"active_node_ids":["agent_1"],"reason":"relevant"}',
            '{"participation":"act","action":"The analyst finds a persistent sample bias.",'
            '"intent":"flag bias","observation":"sample is skewed","self_update":"cautious",'
            '"shared_effect_claim":"The result remains uncertain.","episode_signal":"ready"}',
            json.dumps(
                {
                    "decision": "stop",
                    "guidance": "",
                    "reason": "finding established",
                    "shared_state_summary": "A sample bias constrains the result.",
                    "state_patch": state_patch,
                }
            ),
        ]
    )

    run_id = asyncio.run(WorkflowEngine(provider=provider).run(db=db, workflow=workflow, request=RunRequest(input={})))  # type: ignore[arg-type]
    run = db.query(RunRecord).filter(RunRecord.id == run_id).one()
    output = json.loads(run.output_json)
    event = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.event == "dynamic_state_updated")
        .one()
    )
    event_payload = json.loads(event.payload_json)

    assert "unresolved_sample_bias" in output["simulation"]["dynamic_state"]["concepts"]
    assert event_payload["changed"] is True
    assert event_payload["active_concepts"] == 1
    assert provider.index == 3
