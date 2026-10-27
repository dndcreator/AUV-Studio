from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.action_consistency import admit_action
from app.execution.action_output import ActionOutputError, external_action_output, normalize_action_output
from app.execution.engine import WorkflowEngine
from app.execution.nodes import execute_node
from app.models import MemoryRecord, NodeRunRecord, RunEventRecord, RunRecord
from app.schemas import RunRequest, WorkflowDefinition, WorkflowNode
from app.service import verify_run_audit


VALID = {"participation": "act", "action": "I ask for an answer."}


def parse(output):
    return normalize_action_output(output, round_index=1, action_index=2)


@pytest.mark.parametrize("output", [
    {"content": "not JSON"}, {"content": '{"action": broken JSON'}, {"content": "[]"},
    {"action": "A response without participation"}, {"participation": "act"},
    {"participation": "unknown", "action": "No."}, {"participation": "act", "action": "  "},
    {**VALID, "action": {}}, {**VALID, "intent": []}, {**VALID, "observation": None},
    {**VALID, "result": 1}, {**VALID, "episode_signal": "none"},
    {"content": '{"participation":"act","action":"one","action":"two"}'},
    {"content": '{"participation":"act","action":"one","unknown":NaN}'},
    {"content": json.dumps(VALID), "raw": {"mock": True}},
    {"content": json.dumps(VALID), "raw": {"choices": [{"finish_reason": "length"}]}},
    {"content": json.dumps(VALID), "raw": {"done_reason": "length"}},
])
def test_invalid_actions_never_fall_back_to_text_or_silently_coerce(output):
    with pytest.raises(ActionOutputError) as error:
        parse(output)
    assert error.value.code == "invalid_action_output"
    assert error.value.issues
    assert "broken JSON" not in str(error.value)


@pytest.mark.parametrize("participation", ["observe", "wait"])
def test_inactive_turns_may_have_empty_action(participation):
    assert parse({"participation": participation, "action": ""})["action"] == ""


def test_fenced_valid_action_is_supported_without_redundant_aliases_or_state_writes():
    data = parse({"content": '```json\n' + json.dumps({**VALID, "state_updates": {"invented": True}}) + '\n```'})
    assert data["action"] == data["content"] == VALID["action"]
    assert data["action_index"] == 2 and data["round"] == 1
    assert "simulation_updates" not in data and "state_updates" not in data and "proposal_summary" not in data


class Checker:
    def __init__(self):
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return {"content": json.dumps({"decision": "accept", "reason": "Explicit offline fixture.",
            "scope": {"self_owned": True, "public_only": True}, "findings": []})}


@pytest.mark.parametrize("recover", [True, False])
def test_invalid_builtin_can_regenerate_once_but_is_never_sent_to_semantic_checker(recover):
    provider, records, calls = Checker(), [], []

    async def generate(packet):
        calls.append(packet)
        return VALID if recover and len(calls) == 2 else {"content": "INVALID_SECRET"}

    async def run():
        return await admit_action(provider=provider, runtime_input={"role": {"node_id": "a"},
            "context": {"round": 1, "action_index": 1}}, evidence=[], generate=generate,
            record=records.append, can_regenerate=True)

    if recover:
        assert asyncio.run(run())["action"] == VALID["action"]
    else:
        with pytest.raises(ActionOutputError):
            asyncio.run(run())
    assert len(calls) == len(records) == 2
    assert provider.calls == (1 if recover else 0)
    assert "INVALID_SECRET" not in json.dumps(records)
    assert records[0]["assessment"]["code"] == "invalid_action_output"
    assert "INVALID_SECRET" not in calls[1]["prompt"]


def test_external_text_is_explicitly_adapted_without_inventing_private_intent():
    parsed = parse(external_action_output({"text": "I received the public message."}))
    assert parsed["participation"] == "act"
    assert parsed["intent"] == ""


def test_generic_node_prompt_preserves_state_claim_attribution_instead_of_certifying_it():
    prompts = []

    class Provider:
        async def chat(self, model, system_prompt, user_prompt):
            prompts.append(user_prompt)
            return {"content": "A response."}

    state = {"dynamic_state": {"enabled": True,
        "concepts": {"signature_report": {"status": "active"}},
        "values": {"signature_report": {"value": "A says the king signed.", "status": "confirmed",
            "evidence_kind": "claim", "confidence": 1.0,
            "source_refs": [{"node_id": "a", "action_index": 1, "event_seq": 5}]}}}}
    node = WorkflowNode.model_validate({"id": "b", "type": "agent", "position": {"x": 0, "y": 0}})
    asyncio.run(execute_node(node, {"prompt": "Assess the known evidence.", "_simulation": state}, {}, Provider()))
    prompt = prompts[0]
    assert "not independent verification" in prompt
    assert '"evidence_kind": "claim"' in prompt
    assert '"node_id": "a"' in prompt and '"event_seq": 5' in prompt
    assert "Authoritative Dynamic State" not in prompt and "confirmed" not in prompt


def test_invalid_external_output_does_not_reissue_side_effects():
    provider, calls, records = Checker(), [], []

    async def generate(packet):
        calls.append(packet)
        return external_action_output({"text": '{"action": broken JSON'})

    with pytest.raises(ActionOutputError):
        asyncio.run(admit_action(provider=provider, runtime_input={"context": {"round": 1, "action_index": 1}},
            evidence=[], generate=generate, record=records.append, can_regenerate=False))
    assert len(calls) == len(records) == 1 and provider.calls == 0


@pytest.mark.parametrize("remote_output,valid", [
    ({"text": "I received the message."}, True),
    (VALID, True),
    ({"text": '{"action": broken JSON'}, False),
])
def test_http_external_adapter_uses_strict_admission_without_reissuing_requests(monkeypatch, remote_output, valid):
    calls, records, provider = [], [], Checker()

    def handle(request):
        packet = json.loads(request.content)
        calls.append(packet)
        return httpx.Response(200, json={"task_id": packet["task_id"], "status": "succeeded", "output": remote_output})

    client_type = httpx.AsyncClient
    monkeypatch.setattr("app.execution.nodes.httpx.AsyncClient",
                        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    node = WorkflowNode.model_validate({"id": "external", "type": "external_agent", "position": {"x": 0, "y": 0},
        "config": {"integration_mode": "http", "endpoint_url": "http://agent.test"}})
    runtime_input = {"_participant_turn": True, "prompt": "Act on this message.", "role": {"node_id": "external"},
                     "context": {"round": 1, "action_index": 1}}

    async def generate(packet):
        return await execute_node(node, packet, {}, provider)

    async def run():
        return await admit_action(provider=provider, runtime_input=runtime_input, evidence=[],
                                  generate=generate, record=records.append, can_regenerate=False)

    if valid:
        assert asyncio.run(run())["action"] == remote_output.get("action", remote_output.get("text"))
    else:
        with pytest.raises(ActionOutputError):
            asyncio.run(run())
    assert len(calls) == 1 and len(records) == 1
    assert provider.calls == int(valid)


def test_real_engine_retains_earlier_actions_but_never_publishes_invalid_output():
    class Provider(Checker):
        def __init__(self):
            super().__init__()
            self.generated = {"a": 0, "b": 0}

        async def chat(self, model, system_prompt, user_prompt):
            if "action consistency checker" in system_prompt:
                return await super().chat()
            if "route attention" in system_prompt:
                return {"content": '{"active_node_ids":["a","b"]}'}
            role, _ = json.JSONDecoder().raw_decode(user_prompt.split("ROLE:\n", 1)[1])
            self.generated[role["node_id"]] += 1
            return {"content": json.dumps(VALID) if role["node_id"] == "a" else "INVALID_SECRET"}

    workflow = WorkflowDefinition.model_validate({"id": "strict", "name": "strict", "nodes": [
        {"id": node_id, "type": kind, "position": {"x": 0, "y": 0}} for node_id, kind in
        (("director", "director"), ("a", "agent"), ("b", "agent"))], "edges": [],
        "environment": {"simulation": {"execution_model": "continuous", "horizon_rounds": 1}}})
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider).run(db, workflow, RunRequest()))
        run = db.get(RunRecord, run_id)
        assert run.status == "failed"
        assert len(json.loads(run.output_json)["actions"]) == 1
        assert provider.generated == {"a": 1, "b": 2} and provider.calls == 1
        assert db.query(NodeRunRecord).filter_by(node_id="b").one().output_json == "{}"
        assert db.query(MemoryRecord).filter_by(node_id="b").count() == 0
        assert all("INVALID_SECRET" not in row.payload_json for row in db.query(RunEventRecord))
        assert verify_run_audit(db, run_id).verified
    database.dispose()
