from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.dynamic_state import (
    apply_dynamic_state_proposal,
    compact_dynamic_state,
    init_dynamic_state,
    parse_state_coder_output,
    simulation_state_for_node,
)
from app.execution.engine import WorkflowEngine
from app.execution.simulation_state import init_simulation_state
from app.models import RunEventRecord, RunRecord
from app.schemas import EnvironmentSpec, NodePosition, RunEvent, RunRequest, WorkflowDefinition, WorkflowNode


class SequenceProvider:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.index = 0

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        output = self.outputs[min(self.index, len(self.outputs) - 1)]
        self.index += 1
        return {"content": output, "raw": {"ok": True}}


def _proposal(concept_id: str, *, visibility: str = "global", owner_id: str = "") -> dict:
    return {
        "schema_ops": [
            {
                "op": "upsert",
                "id": concept_id,
                "description": "A newly discovered condition that constrains a later decision.",
                "scope": "entity" if owner_id else "global",
                "owner_id": owner_id,
                "value_type": "text",
                "visibility": visibility,
                "retention": "Until the dependent decision is resolved.",
                "future_relevance": "Forgetting it would change which later action is reasonable.",
                "reason": "The latest action created a durable unresolved consequence.",
            }
        ],
        "state_ops": [
            {
                "op": "set",
                "concept_id": concept_id,
                "value": "active",
                "confidence": 0.82,
                "reason": "Explicitly established by the action.",
                "source_node_ids": [owner_id or "agent_1"],
            }
        ],
    }


def test_state_coder_discovers_arbitrary_concepts_without_domain_schema() -> None:
    state = init_dynamic_state({"enabled": True})
    updated, result = apply_dynamic_state_proposal(
        state,
        _proposal("unpublished_methodological_risk"),
        seq=4,
        source_node_ids=["analyst_1"],
    )

    assert result["changed"] is True
    assert set(updated["concepts"]) == {"unpublished_methodological_risk"}
    assert updated["values"]["unpublished_methodological_risk"]["value"] == "active"
    assert updated["history"][0]["source_node_ids"] == ["analyst_1"]


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
        }
    )

    updated, result = apply_dynamic_state_proposal(state, proposal, seq=2, source_node_ids=["agent_1"])

    assert set(updated["concepts"]) == {"first_constraint"}
    assert {item["reason"] for item in result["rejected"]} >= {"round_budget", "missing_relevance_evidence", "unknown_concept"}


def test_private_dynamic_concepts_are_visible_only_to_their_owner() -> None:
    state = init_dynamic_state({"enabled": True})
    updated, _ = apply_dynamic_state_proposal(
        state,
        _proposal("concealed_intention", visibility="private", owner_id="agent_1"),
        seq=3,
        source_node_ids=["agent_1"],
    )
    simulation = {"mode": "roleplay", "dynamic_state": updated}

    owner_view = simulation_state_for_node(simulation, "agent_1")["dynamic_state"]
    peer_view = simulation_state_for_node(simulation, "agent_2")["dynamic_state"]
    public_view = compact_dynamic_state(updated)

    assert "concealed_intention" in owner_view["concepts"]
    assert "concealed_intention" not in peer_view["concepts"]
    assert "concealed_intention" not in public_view["concepts"]


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
    state_patch = _proposal("unresolved_sample_bias")
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
