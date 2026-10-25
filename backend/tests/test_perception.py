from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.engine import WorkflowEngine
from app.execution.context_book import build_scene_context_pack
from app.execution.graph import WorkflowValidationError, validate_workflow
from app.execution.memory import select_memories_for_node
from app.execution.perception import actions_for_node, delivery_receipts, incoming_interactions, participant_context, public_action
from app.models import NodeRunRecord, RunRecord
from app.schemas import EnvironmentSpec, RunRequest, WorkflowDefinition, WorkflowEdge
from app.service import verify_run_audit


def sample_action():
    return {"node_id": "a", "actor": "A", "action": "PUBLIC_FINDING", "action_index": 1, "event_seq": 2,
            "intent": "SECRET_INTENT", "observation": "SECRET_OBSERVATION", "self_update": "SECRET_STATE",
            "turn_delta": "SECRET_DELTA", "shared_effect_claim": "SECRET_CLAIM", "future_field": "SECRET_FUTURE"}


def connection(source="a", target="b", **kwargs):
    return WorkflowEdge(id=f"{source}_{target}", source=source, target=target, **kwargs)


def test_public_projection_and_directed_delivery_do_not_share_internal_fields():
    action = sample_action()
    edges = [connection(interaction={"mode": "report", "relation": "manager", "template": "Result: {{source_output}}"})]
    assert "SECRET" not in json.dumps(public_action(action))
    assert actions_for_node([action], "c", edges) == []
    assert actions_for_node([action], "a", edges)[0]["intent"] == "SECRET_INTENT"
    received = actions_for_node([action], "b", edges)
    assert received[0]["action"] == "PUBLIC_FINDING" and "SECRET" not in json.dumps(received)
    messages = incoming_interactions(received, "b", edges)
    assert messages[0]["message"] == "Result: PUBLIC_FINDING"
    assert messages[0]["relation"] == "manager" and messages[0]["mode"] == "report"
    assert messages[0]["event_seq"] == 2 and "SECRET" not in json.dumps(messages)
    assert "PUBLIC_FINDING" in json.dumps(actions_for_node([action], "c", []))
    assert "SECRET" not in json.dumps(actions_for_node([action], "c", []))
    assert action["future_field"] == "SECRET_FUTURE"
    assert delivery_receipts([{"node_id": "a"}, {"node_id": "b", "received_event_seqs": []}]) == [
        {"node_id": "a", "action_index": None, "received_event_seqs": None},
        {"node_id": "b", "action_index": None, "received_event_seqs": []},
    ]


def test_conditional_messages_and_template_context_cannot_bypass_projection():
    action = {**sample_action(), "result": False}
    edges = [connection(condition="true")]
    assert actions_for_node([action], "b", edges) == []
    visible = actions_for_node([{**action, "result": True}], "b", edges)
    context = participant_context({"a": sample_action(), "_memory": [{"content": "SECRET"}], "_private": "SECRET"},
                                  state={}, memories=[], background={}, actions=visible, guidance="")
    assert "SECRET" not in json.dumps(context)
    assert context["a"]["content"] == "PUBLIC_FINDING"


def test_character_memories_are_owned_even_with_high_relevance_and_global_scope():
    wf = make_workflow([])
    node = next(node for node in wf.nodes if node.id == "b")
    memories = [{"kind": "character", "node_id": "a", "subject": "b", "scope": "global", "importance": 1,
                 "content": "SECRET"}, {"kind": "character", "node_id": "b", "content": "own history"}]
    selected = select_memories_for_node(memories, node)
    assert len(selected) == 1 and selected[0]["content"] == "own history"


def test_private_intent_does_not_activate_a_shared_background_entry():
    environment = EnvironmentSpec.model_validate({"context_book": {"entries": [{
        "id": "secret_trigger", "content": "A shared entry that must not reveal a private trigger.",
        "activation": {"always": False, "keywords": ["SECRET_INTENT"]},
    }]}})
    pack = build_scene_context_pack(environment=environment, state={}, recent_actions=[sample_action()])
    assert pack["entries"] == []


def make_workflow(edges) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "perception", "name": "perception", "nodes": [
            {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0},
             "config": {"entity_name": node_id, "system_prompt": "TEMPLATE {{a.intent}} {{a.observation}} {{a.content}}"},
             "inputs": {"probe": "{{a.intent}}", "public_probe": "{{a.content}}"}}
            for node_id, node_type in [("director", "director"), ("a", "agent"), ("b", "agent"), ("c", "agent")]],
        "edges": edges, "entry_nodes": ["director"],
        "environment": {"simulation": {"execution_model": "continuous", "horizon_rounds": 2,
                                         "dynamic_state": {"enabled": False}}},
    })


def test_continuous_allows_bidirectional_connections_but_dag_still_rejects_cycles():
    wf = make_workflow([connection().model_dump(), connection("b", "a").model_dump()])
    validate_workflow(wf)
    dag = wf.model_copy(deep=True)
    dag.environment.simulation["execution_model"] = "dag"
    with pytest.raises(WorkflowValidationError, match="cycle"):
        validate_workflow(dag)


@pytest.mark.parametrize("connected", [False, True])
def test_real_continuous_runtime_isolates_history_templates_and_owner_recall(connected):
    wf = make_workflow([connection().model_dump(), connection("b", "a").model_dump()] if connected else [])

    class Provider:
        def __init__(self):
            self.calls = []

        async def chat(self, model, system_prompt, user_prompt):
            self.calls.append((system_prompt, user_prompt))
            if "route attention" in system_prompt:
                return {"content": '{"active_node_ids":["a","b","c"]}'}
            if "continuity auditor" in system_prompt:
                return {"content": '{"decision":"continue"}'}
            if '"node_id": "a"' in user_prompt.split("ROLE:\n")[-1]:
                return {"content": json.dumps({key: value for key, value in sample_action().items() if key != "future_field"})}
            return {"content": '{"action":"A normal public response."}'}

    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider=provider).run(db, wf, RunRequest()))
        assert db.get(RunRecord, run_id).status == "succeeded"
        rows = db.query(NodeRunRecord).filter_by(run_id=run_id).order_by(NodeRunRecord.id).all()
        for row in rows:
            data = json.loads(row.input_json)
            if row.node_id in {"b", "c"}:
                assert "SECRET" not in row.input_json
                assert data["probe"] is None
                assert all("role" not in peer for peer in data["context"]["communication"]["known_participants"]
                           if peer["node_id"] != row.node_id)
            if row.node_id == "c" and connected:
                assert "PUBLIC_FINDING" not in row.input_json
                assert data["context"]["communication"]["outgoing"] == []
            if row.node_id == "b":
                assert "PUBLIC_FINDING" in row.input_json
                assert bool(data["_interactions"]) is connected
        a_rows = [row for row in rows if row.node_id == "a"]
        assert "SECRET_INTENT" in a_rows[1].input_json
        routers = [user for system, user in provider.calls if "route attention" in system]
        assert all("SECRET" not in prompt for prompt in routers)
        audits = [user for system, user in provider.calls if "continuity auditor" in system]
        assert "SECRET_INTENT" in audits[0]
        peer_calls = [(system, user) for system, user in provider.calls if '"node_id": "b"' in user.split("ROLE:\n")[-1] and "autonomous participant" in user]
        assert peer_calls and all("SECRET" not in system + user for system, user in peer_calls)
        assert verify_run_audit(db, run_id).verified
    database.dispose()


def test_external_agent_http_envelope_receives_only_delivered_public_information(monkeypatch):
    import httpx

    wf = make_workflow([connection().model_dump()])
    wf.nodes = [node for node in wf.nodes if node.id != "c"]
    wf.environment.simulation["horizon_rounds"] = 1
    external = next(node for node in wf.nodes if node.id == "b")
    external.type = "external_agent"
    external.config = {"integration_mode": "http", "endpoint_url": "http://127.0.0.1:8787",
                       "prompt_template": "{{prompt}} {{interactions}}"}
    requests = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, headers, json):
            requests.append(json)
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "task_id": json["task_id"], "status": "succeeded", "output": {"text": "Received public work."},
            })

    class Provider:
        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                return {"content": '{"active_node_ids":["a","b"]}'}
            if "continuity auditor" in system_prompt:
                return {"content": '{"decision":"stop"}'}
            return {"content": json.dumps(sample_action())}

    monkeypatch.setattr("app.execution.nodes.httpx.AsyncClient", Client)
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider=Provider()).run(db, wf, RunRequest()))
        assert db.get(RunRecord, run_id).status == "succeeded"
        assert len(requests) == 1
        assert "PUBLIC_FINDING" in json.dumps(requests[0])
        assert "SECRET" not in json.dumps(requests[0])
        assert requests[0]["context"]["interactions"][0]["source"] == "a"
        assert verify_run_audit(db, run_id).verified
    database.dispose()
