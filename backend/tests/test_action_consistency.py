from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.action_consistency import (
    ActionConsistencyError, CATEGORIES, CHECK_SYSTEM_PROMPT, admit_action, build_consistency_evidence, parse_assessment,
)
from app.execution.engine import WorkflowEngine
from app.models import MemoryRecord, NodeRunRecord, RunEventRecord, RunRecord
from app.schemas import RunRequest, WorkflowDefinition
from app.service import get_events, get_run, verify_run_audit, _build_readable_report_source


ACCEPT = {"decision": "accept", "reason": "No known contradiction.", "scope": {"self_owned": True, "public_only": True}, "findings": []}


def verdict(category="spacetime", decision="conflict", refs=None):
    return {"decision": decision, "reason": "The claimed result lacks established prerequisites.", "scope": ACCEPT["scope"], "findings": [
        {"category": category, "reason": "Check the cited constraint.", "evidence_ids": refs or ["environment.constraints.0"]}]}


def workflow():
    return WorkflowDefinition.model_validate({
        "id": "consistency", "name": "consistency", "nodes": [
            {"id": "director", "type": "director", "position": {"x": 0, "y": 0}},
            {"id": "a", "type": "agent", "position": {"x": 0, "y": 0},
             "config": {"entity_name": "A", "entity_profile": "Lives in year 500.", "model_api_key": "TRANSPORT-SECRET"}},
            {"id": "b", "type": "agent", "position": {"x": 0, "y": 0},
             "config": {"entity_name": "B", "entity_profile": "Lives in year 2000; privately knows PRIVATE-K7."}},
        ], "edges": [{"id": "ab", "source": "a", "target": "b"}], "entry_nodes": ["director"],
        "environment": {"constraints": ["No time travel or communication across eras."],
                        "simulation": {"execution_model": "continuous", "horizon_rounds": 1}},
    })


def test_checking_projection_pins_inactive_rules_and_other_entity_constraints_without_credentials():
    wf = workflow()
    wf.environment.context_book.entries = wf.environment.context_book.model_validate({"entries": [{
        "id": "old_law", "kind": "rule", "content": "An object destroyed earlier stays destroyed.",
        "activation": {"always": False, "keywords": ["never-triggered"]},
        "visibility": {"scope": "private", "node_ids": ["b"]},
    }]}).entries
    state = {"dynamic_state": {"enabled": True, "concepts": {
        "b_secret": {"status": "active", "visibility": "private", "owner_id": "b"},
        "old": {"status": "retired"}}, "values": {"b_secret": {"value": "PRIVATE-DURABLE-STATE"}}}}
    evidence = build_consistency_evidence(wf, state, [], {"role": {"node_id": "a"}})
    by_id = {row["id"]: row for row in evidence}
    assert "book.old_law" in by_id and "entity.b" in by_id
    assert "year 2000" in json.dumps(by_id["entity.b"])
    assert "TRANSPORT-SECRET" not in json.dumps(evidence)
    assert "PRIVATE-DURABLE-STATE" in json.dumps(by_id["dynamic.b_secret"])
    assert "dynamic.old" not in by_id
    assert "PRIVATE-DURABLE-STATE" not in json.dumps(by_id["actor.input"])
    assert all(category in CHECK_SYSTEM_PROMPT for category in CATEGORIES)
    assert "content ratings" in CHECK_SYSTEM_PROMPT and "lies" in CHECK_SYSTEM_PROMPT


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"decision":"accept"}',
    json.dumps({**ACCEPT, "decision": []}), json.dumps(verdict(category=[])),
    json.dumps({**ACCEPT, "findings": [{"category": "spacetime"}]}),
    json.dumps(verdict(refs=["fabricated.reference"])), json.dumps(verdict("personality")),
    json.dumps({**verdict(), "findings": []})])
def test_malformed_or_ungrounded_assessments_never_accept(content):
    with pytest.raises(ActionConsistencyError):
        parse_assessment(content, [{"id": "environment.constraints.0"}])


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_all_six_categories_use_the_same_protocol(category):
    evidence = [{"id": "environment.constraints.0"}]
    assert parse_assessment(json.dumps(verdict(category)), evidence)["decision"] == "conflict"
    assert parse_assessment(json.dumps(ACCEPT), evidence)["decision"] == "accept"


@pytest.mark.parametrize("decision", ["conflict", "uncertain"])
def test_repair_is_bounded_and_does_not_disclose_auditor_private_evidence(decision):
    inputs, records = [], []
    evidence = [{"id": "entity.b", "value": "PRIVATE-K7"}]

    class Provider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            assert "PRIVATE-K7" in kwargs["user_prompt"]
            response = verdict("knowledge", decision, ["entity.b"])
            response["reason"] = "PRIVATE-K7 must stay private."
            return {"content": json.dumps(response)}

    async def generate(value):
        inputs.append(value)
        return {"participation": "act", "action": "Unsupported completed effect."}

    provider = Provider()
    with pytest.raises(ActionConsistencyError, match="bounded"):
        asyncio.run(admit_action(provider=provider, runtime_input={"context": {"round": 1, "action_index": 1}, "prompt": "Act."},
            evidence=evidence, generate=generate, record=records.append, can_regenerate=True))
    assert len(inputs) == provider.calls == len(records) == 2
    assert "PRIVATE-K7" not in json.dumps(inputs)
    assert records[0]["cited_evidence"] == evidence
    assert "context_digest" in records[0]


def test_context_limit_stops_before_paid_check_instead_of_truncating(monkeypatch):
    monkeypatch.setattr("app.execution.action_consistency.settings", SimpleNamespace(action_check_max_chars=1))
    records = []

    class Provider:
        async def chat(self, **kwargs):
            pytest.fail("Oversized evidence must not reach the provider")

    async def generate(value):
        return {"participation": "act", "action": "A harmless action."}

    with pytest.raises(ActionConsistencyError, match="silently truncated"):
        asyncio.run(admit_action(provider=Provider(), runtime_input={"context": {"round": 1, "action_index": 1}}, evidence=[],
            generate=generate, record=records.append, can_regenerate=True))
    assert records[0]["assessment"]["decision"] == "error"


@pytest.mark.parametrize("mode", ["repair", "conflict", "uncertain", "invalid", "transport", "budget"])
def test_real_engine_admits_only_checked_actions_and_keeps_rejected_candidates_out_of_memory_and_reports(mode):
    from app.evaluation.budget import EvalBudgetExceeded

    wf = workflow()

    class Provider:
        generated = 0
        checks = 0
        audits = []

        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                return {"content": '{"active_node_ids":["a","b"]}'}
            if system_prompt == CHECK_SYSTEM_PROMPT:
                self.checks += 1
                if mode == "transport":
                    raise TimeoutError("PRIVATE-K7 checker unavailable")
                if mode == "budget":
                    raise EvalBudgetExceeded("test budget exhausted")
                if mode == "invalid":
                    return {"content": "not json"}
                checking = json.loads(user_prompt)
                assert "TRANSPORT-SECRET" not in user_prompt
                assert "year 2000" in user_prompt
                response = ACCEPT if mode == "repair" and checking["CANDIDATE"]["action"] != "IMPOSSIBLE-CONTACT" else verdict(
                    decision="uncertain" if mode == "uncertain" else "conflict")
                return {"content": json.dumps(response)}
            if "continuity auditor" in system_prompt:
                assert "IMPOSSIBLE-CONTACT" not in user_prompt
                self.audits.append(user_prompt)
                return {"content": '{"decision":"stop","task_complete":true,"completion_reason":"Offline fixture goal reached."}'}
            self.generated += 1
            if self.generated <= 2:
                assert "PRIVATE-K7" not in user_prompt
            if self.generated == 1:
                return {"content": json.dumps({"participation": "act", "action": "IMPOSSIBLE-CONTACT", "intent": "Go to B's house across eras."})}
            assert "IMPOSSIBLE-CONTACT" not in user_prompt
            return {"content": json.dumps({"participation": "act", "action": "I write a note without claiming it reached anyone.", "intent": "Consider a possible contact."})}

    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider).run(db, wf, RunRequest()))
        run = db.get(RunRecord, run_id)
        output = json.loads(run.output_json)
        successful = mode == "repair"
        assert run.status == ("succeeded" if successful else "failed"), run.error_json
        assert len(output["actions"]) == (2 if successful else 0)
        assert "IMPOSSIBLE-CONTACT" not in run.output_json
        if mode == "transport":
            assert "PRIVATE-K7" not in run.error_json
        assert all("IMPOSSIBLE-CONTACT" not in row.content for row in db.query(MemoryRecord))
        detail = get_run(db, run_id)
        assert "IMPOSSIBLE-CONTACT" not in _build_readable_report_source(detail, wf)
        checks = db.query(RunEventRecord).filter_by(run_id=run_id, event="action_checked").order_by(RunEventRecord.seq).all()
        expected = 3 if successful else 2 if mode in {"conflict", "uncertain", "invalid"} else 1
        assert provider.checks == len(checks) == expected, run.error_json
        assert "IMPOSSIBLE-CONTACT" in checks[0].payload_json
        succeeded = db.query(RunEventRecord).filter_by(run_id=run_id, event="succeeded").all()
        assert all(row.seq > checks[0].seq and "IMPOSSIBLE-CONTACT" not in row.payload_json for row in succeeded)
        if successful:
            assert json.loads(succeeded[0].payload_json)["_trace"]["parent_event_ids"] == [checks[1].seq]
        assert all(row.output_json == "{}" for row in db.query(NodeRunRecord).filter_by(status="failed"))
        assert len(provider.audits) == (1 if successful else 0)
        assert any(event.event == "action_checked" for event in get_events(db, run_id, after_seq=0, limit=100).events)
        assert verify_run_audit(db, run_id).verified
    database.dispose()


def test_external_agent_conflict_is_not_automatically_reissued(monkeypatch):
    wf = workflow()
    wf.nodes[1].type = "external_agent"
    calls = []

    async def execute_node(**kwargs):
        calls.append(kwargs["node"].id)
        return {"participation": "act", "action": "IMPOSSIBLE-CONTACT"}

    class Provider:
        async def chat(self, model, system_prompt, user_prompt):
            return {"content": json.dumps(verdict() if system_prompt == CHECK_SYSTEM_PROMPT else {"active_node_ids": ["a"]})}

    monkeypatch.setattr("app.execution.engine.execute_node", execute_node)
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(Provider()).run(db, wf, RunRequest()))
        assert db.get(RunRecord, run_id).status == "failed"
        assert calls == ["a"]
        assert db.query(RunEventRecord).filter_by(event="action_checked").count() == 1
        assert db.query(MemoryRecord).count() == 0
    database.dispose()


def test_invalid_protocol_recovers_once_without_regenerating_external_actor():
    calls, records, checking_inputs = [], [], []

    async def generate(value):
        calls.append(value)
        return {"participation": "act", "action": "I wait and do not claim another actor's response."}

    class Provider:
        async def chat(self, **kwargs):
            checking_inputs.append(kwargs["user_prompt"])
            return {"content": json.dumps({"decision": "accept", "scope": ACCEPT["scope"], "findings": []} if len(checking_inputs) == 1 else ACCEPT)}

    result = asyncio.run(admit_action(provider=Provider(), runtime_input={"context": {"round": 1, "action_index": 1}},
        evidence=[], generate=generate, record=records.append, can_regenerate=False))
    assert result["action"].startswith("I wait")
    assert len(calls) == 1 and len(checking_inputs) == 2
    assert [row["check_attempt"] for row in records] == [1, 2]
    assert "reason_type=NoneType" in records[0]["diagnostic"]
    assert records[0]["context_digest"] != records[1]["context_digest"]
    assert checking_inputs[1].startswith(checking_inputs[0])


@pytest.mark.parametrize("limited_context", [True, False])
def test_protocol_recovery_respects_context_and_physical_request_budgets(monkeypatch, limited_context):
    from app.evaluation.budget import BudgetedProvider, EvalBudget
    records, calls = [], []

    class Provider:
        async def chat(self, **kwargs):
            calls.append(kwargs)
            if limited_context:
                monkeypatch.setattr("app.execution.action_consistency.settings", SimpleNamespace(
                    action_check_max_chars=len(kwargs["user_prompt"]) + 1))
            return {"content": "invalid"}

    async def generate(value):
        return {"participation": "act", "action": "I wait."}

    provider = BudgetedProvider(Provider(), EvalBudget(max_calls=1, max_total_tokens=10000, max_cost_usd=1))
    with pytest.raises(ActionConsistencyError):
        asyncio.run(admit_action(provider=provider, runtime_input={"context": {"round": 1, "action_index": 1}},
            evidence=[], generate=generate, record=records.append, can_regenerate=False))
    assert provider.usage.calls == len(calls) == 1
    assert len(records) == 2 and records[-1]["assessment"]["decision"] == "error"
