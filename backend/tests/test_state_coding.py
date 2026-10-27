from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from app.execution.action_boundary import ActionBoundaryError, validate_actor_context
from app.execution.action_consistency import build_consistency_evidence
from app.execution.checkpoints import continuous_snapshot, restore_continuous_checkpoint
from app.execution.dynamic_state import init_dynamic_state, query_dynamic_state, simulation_state_for_node
from app.execution.engine import WorkflowEngine
from app.execution.scene_control import scene_audit_instruction
from app.execution.state_coding import StateCodingError, apply_state_batch, encode_state_batch, pending_state_actions, proposal_from_response
from app.models import RunEventRecord, RunRecord
from app.schemas import RunRequest, WorkflowDefinition
from app.service import verify_run_audit


EMPTY = {"schema_ops": [], "state_ops": []}


def action(seq=10, node="a"):
    return {"node_id": node, "event_seq": seq, "action_index": seq, "round": 1,
            "participation": "act", "action": f"Own action {seq}.", "intent": "PRIVATE-PLAN"}


def patch(source, *, concept="position", subject="a", predicate="location", value="hall", private=False, kind="action"):
    ref = {key: source[key] for key in ("node_id", "event_seq", "action_index")}
    return {"schema_ops": [{"op": "upsert", "id": concept, "description": "A durable source-linked constraint.",
            "subject_id": subject, "predicate": predicate, "scope": "entity", "owner_id": source["node_id"],
            "visibility": "private" if private else "global", "value_type": "text", "retention": {"mode": "persistent"},
            "future_relevance": "May constrain a later action.", "reason": "Established in this source.", "source_refs": [ref]}],
            "state_ops": [{"op": "set", "concept_id": concept, "value": value, "evidence_kind": kind,
                           "confidence": 1, "reason": "Source attribution.", "source_refs": [ref]}]}


def commit(current, proposal, actions):
    return apply_state_batch(current, proposal, actions=actions, seq=len(actions), round_index=1)


def workflow():
    return WorkflowDefinition.model_validate({"id": "coding", "name": "coding", "nodes": [
        {"id": key, "type": kind, "position": {"x": 0, "y": 0}} for key, kind in
        (("director", "director"), ("a", "agent"), ("b", "agent"))], "edges": [
            {"id": "ab", "source": "a", "target": "b"}],
        "environment": {"simulation": {"execution_model": "continuous", "horizon_rounds": 2}}})


def test_index_queries_follow_updates_without_equating_subject_with_private_owner():
    state, result = commit(init_dynamic_state({"enabled": True}), patch(action(), subject="door", private=True), [action()])
    assert result["coding"]["status"] == "coded"
    view = query_dynamic_state(state, subject_id="door", predicate="location", node_id="a")
    assert view["position"]["current"]["value"] == "hall"
    assert query_dynamic_state(state, subject_id="door", node_id="b") == {}
    assert query_dynamic_state(state, subject_id="door") == {}
    proposal = patch(action(20), subject="door", value="yard", private=True)
    next_state, _ = commit(state, proposal, [action(20)])
    assert query_dynamic_state(next_state, predicate="location", node_id="a")["position"]["current"]["value"] == "yard"
    assert state["values"]["position"]["value"] == "hall"


@pytest.mark.parametrize("field,value", [("subject_id", {}), ("predicate", ""), ("predicate", "x" * 81)])
def test_bad_indexes_roll_back_the_whole_batch(field, value):
    initial = init_dynamic_state({"enabled": True})
    proposal = patch(action())
    proposal["schema_ops"][0][field] = value
    state, result = commit(initial, proposal, [action()])
    assert state == initial and result["rejected"] and result["accepted_schema_ops"] == []


def test_optional_indexes_are_preserved_and_cannot_be_retargeted():
    proposal = patch(action())
    for field in ("subject_id", "predicate"):
        proposal["schema_ops"][0].pop(field)
    state, _ = commit(init_dynamic_state({"enabled": True}), proposal, [action()])
    state, _ = commit(state, patch(action(20)), [action(20)])
    omitted = patch(action(30))
    omitted["schema_ops"][0].pop("predicate")
    state, _ = commit(state, omitted, [action(30)])
    changed = patch(action(40), predicate="another_attribute")
    rejected, result = commit(state, changed, [action(40)])
    assert rejected == state and result["rejected"]
    assert state["concepts"]["position"]["predicate"] == "location"


@pytest.mark.parametrize("proposal", [None, {}, {"schema_ops": {}, "state_ops": []}, {"parse_error": "invalid_json"}])
def test_invalid_patch_is_not_an_empty_acknowledgment(proposal):
    initial = init_dynamic_state({"enabled": True})
    state, result = commit(initial, proposal, [action()])
    assert state == initial and result["coding"]["status"] == "pending"


def test_partial_rejection_is_atomic_and_explicit_empty_patch_acknowledges_without_new_facts():
    initial = init_dynamic_state({"enabled": True})
    proposal = patch(action())
    proposal["state_ops"].append({"op": "set", "concept_id": "missing"})
    state, result = commit(initial, proposal, [action()])
    assert state == initial and not result["changed"] and not result["accepted_state_ops"]
    state, result = commit(state, EMPTY, [action()])
    assert state["last_coded_event_seq"] == 10 and not state["concepts"]
    assert result["coding"]["status"] == "coded" and result["changed"] is False
    assert pending_state_actions(state, [action()]) == []


@pytest.mark.parametrize("response", [
    {"content": '{"state_patch":{"schema_ops":[],"state_ops":[]},"state_patch":{}}'},
    {"content": '{"state_patch":{"schema_ops":[],"state_ops":[],"unused":NaN}}'},
    {"content": json.dumps({"state_patch": EMPTY}), "raw": {"mock": True}},
    {"content": json.dumps({"state_patch": EMPTY}), "raw": {"choices": [{"finish_reason": "length"}]}},
])
def test_unusable_audit_cannot_turn_into_a_successful_empty_coding_ack(response):
    proposal = proposal_from_response(response, director_envelope=True)
    assert proposal.get("parse_error")
    state, result = commit(init_dynamic_state({"enabled": True}), proposal, [action()])
    assert state["last_coded_event_seq"] == 0 and result["coding"]["status"] == "pending"


def test_model_cannot_choose_the_coding_cursor_or_point_it_outside_this_branch():
    state, _ = commit(init_dynamic_state({"enabled": True}), {**EMPTY, "last_coded_event_seq": 999}, [action()])
    assert state["last_coded_event_seq"] == 10
    with pytest.raises(StateCodingError, match="branch"):
        pending_state_actions({**state, "last_coded_event_seq": 999}, [action()])


def test_claim_keeps_attribution_and_does_not_certify_the_reported_consequence():
    source = {**action(), "action": "A claims that B died."}
    state, _ = commit(init_dynamic_state({"enabled": True}), patch(source, concept="a_report", subject="a",
        predicate="reported_information", value="A claims B died; not independently established.", kind="claim"), [source])
    record = query_dynamic_state(state, subject_id="a")["a_report"]["current"]
    assert record["status"] == "recorded" and record["evidence_kind"] == "claim"
    assert query_dynamic_state(state, subject_id="b", predicate="alive") == {}


def test_cursor_uses_all_pending_branch_events_including_ones_older_than_recent_window():
    actions = [action(seq) for seq in range(1, 26)]
    state = {"dynamic_state": {**init_dynamic_state({"enabled": True}), "last_coded_event_seq": 3}}
    evidence = build_consistency_evidence(workflow(), state, actions, {"role": {"node_id": "a"}})
    events = [row for row in evidence if row["id"].startswith("event.")]
    assert {row["id"] for row in events} == {f"event.{seq}" for seq in range(4, 26)}
    assert "PRIVATE-PLAN" not in json.dumps(events)
    assert pending_state_actions(state["dynamic_state"], actions) == actions[3:]
    assert pending_state_actions(state["dynamic_state"], actions[:5]) == actions[3:5]
    prompt = scene_audit_instruction(state=state, scene_control={"last_audited_round": 10}, recent_actions=actions,
        round_index=11, max_actions=30, uncoded_actions=actions[3:])
    assert prompt.count('"action": "Own action 4."') == 1


@pytest.mark.parametrize("actions", [[action(), action()], [action(20), action(10)], [{"node_id": "a"}]])
def test_bad_action_ledger_cannot_advance_the_cursor(actions):
    with pytest.raises(StateCodingError):
        pending_state_actions(init_dynamic_state({"enabled": True}), actions)


@pytest.mark.parametrize("repair", [EMPTY, {"schema_ops": "bad", "state_ops": []}])
def test_one_bounded_repair_never_replays_actors_or_advances_a_failed_cursor(repair):
    class Provider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return {"content": json.dumps(repair)}

    provider, records = Provider(), []
    initial = init_dynamic_state({"enabled": True})
    task = encode_state_batch(provider=provider, state={"dynamic_state": initial}, proposal={}, actions=[action()],
        seq=1, round_index=1, record=lambda state, result: records.append((state, result)))
    if repair == EMPTY:
        asyncio.run(task)
        assert records[-1][0]["last_coded_event_seq"] == 10
    else:
        with pytest.raises(StateCodingError):
            asyncio.run(task)
        assert records[-1][0] == initial
    assert provider.calls == 1 and len(records) == 2 and initial["last_coded_event_seq"] == 0


@pytest.mark.parametrize("budget_blocked", [True, False])
def test_repair_uses_the_existing_budget_and_transport_failures_are_not_swallowed(budget_blocked):
    class Provider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise RuntimeError("offline transport failure")

    inner, records = Provider(), []
    provider = BudgetedProvider(inner, EvalBudget(max_calls=0 if budget_blocked else 1))
    initial = init_dynamic_state({"enabled": True})
    with pytest.raises(EvalBudgetExceeded if budget_blocked else RuntimeError):
        asyncio.run(encode_state_batch(provider=provider, state={"dynamic_state": initial}, proposal={},
            actions=[action()], seq=1, round_index=1, record=lambda state, result: records.append(result)))
    assert len(records) == 1 and records[0]["coding"]["status"] == "pending"
    assert inner.calls == int(not budget_blocked) and initial["last_coded_event_seq"] == 0
    if not budget_blocked:
        assert provider.usage_by_kind["state_repair"]["failed"] == 1


def test_checkpoint_commit_failure_does_not_publish_a_new_cursor_or_request_repair():
    class Provider:
        async def chat(self, **kwargs):
            pytest.fail("Persistence failures must not trigger model repair.")

    initial = init_dynamic_state({"enabled": True})

    def fail_commit(state, result):
        assert state["last_coded_event_seq"] == 10
        raise RuntimeError("offline checkpoint commit failure")

    with pytest.raises(RuntimeError, match="commit failure"):
        asyncio.run(encode_state_batch(provider=Provider(), state={"dynamic_state": initial}, proposal=EMPTY,
            actions=[action()], seq=1, round_index=1, record=fail_commit))
    assert initial["last_coded_event_seq"] == 0


def test_checkpoint_is_detached_and_restores_uncoded_events_not_list_length():
    context = {"_simulation": {"dynamic_state": init_dynamic_state({"enabled": True})}}
    actions = [action()]
    snapshot = continuous_snapshot(context, actions, 1, 1, 1)
    context["_simulation"]["dynamic_state"]["last_coded_event_seq"] = 10
    actions.append(action(20))
    restored = {}
    restore_continuous_checkpoint(restored, RunRequest(input={"_rollback": {"continuous_snapshot": snapshot}}))
    assert pending_state_actions(restored["_simulation"]["dynamic_state"], restored["_continuous_resume"]["actions"]) == [action()]


@pytest.mark.parametrize("violation", ["actor", "reference", "channel", "private", "altered"])
def test_engine_input_precheck_rejects_only_explicit_reference_or_visibility_violations(violation):
    source = action()
    dynamic, _ = commit(init_dynamic_state({"enabled": True}), patch(source, private=True), [source])
    state = {"dynamic_state": dynamic}
    packet = {"role": {"node_id": "b"}, "context": {"state": simulation_state_for_node(state, "b"),
              "recent_actions": [{key: value for key, value in source.items() if key != "intent"}], "interactions": []}}
    validate_actor_context(workflow(), state, [source], packet)
    if violation == "actor":
        packet["role"]["node_id"] = "unknown"
    elif violation == "reference":
        packet["context"]["recent_actions"][0]["event_seq"] = 999
    elif violation == "channel":
        packet["context"]["interactions"] = [{"event_seq": 10, "edge_id": "reverse"}]
    elif violation == "private":
        packet["context"]["state"]["dynamic_state"] = dynamic
    else:
        packet["context"]["recent_actions"][0]["action"] = "A newly invented reply."
    with pytest.raises(ActionBoundaryError):
        validate_actor_context(workflow(), state, [source], packet)


@pytest.mark.parametrize("repaired", [True, False])
def test_real_engine_commits_coding_and_cursor_together_and_fails_closed_after_repair(repaired):
    class Provider:
        repairs = 0

        async def chat(self, model, system_prompt, user_prompt):
            if "route attention" in system_prompt:
                data = {"active_node_ids": ["a"]}
            elif "action consistency checker" in system_prompt:
                data = {"decision": "accept", "reason": "Offline fixture only.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
            elif "continuity auditor" in system_prompt:
                data = {"decision": "stop", "task_complete": True, "completion_reason": "Fixture finished.", "state_patch": {}}
            elif "State Coder repairing" in system_prompt:
                self.repairs += 1
                packet, _ = json.JSONDecoder().raw_decode(user_prompt.split("CONTEXT:\n", 1)[1])
                data = patch(packet["new_actions"][0]) if repaired else {}
            else:
                data = {"participation": "act", "action": "I enter the hall."}
            return {"content": json.dumps(data)}

    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    provider = Provider()
    with sessionmaker(bind=database)() as db:
        run_id = asyncio.run(WorkflowEngine(provider).run(db, workflow(), RunRequest()))
        run = db.get(RunRecord, run_id)
        assert run.status == ("succeeded" if repaired else "failed")
        output = json.loads(run.output_json)
        accepted_seq = output["actions"][0]["event_seq"]
        dynamic = output["simulation"]["dynamic_state"]
        assert dynamic["last_coded_event_seq"] == (accepted_seq if repaired else 0)
        events = db.query(RunEventRecord).filter_by(run_id=run_id, event="dynamic_state_updated").order_by(RunEventRecord.seq).all()
        assert len(events) == 2 and provider.repairs == 1
        for event in events:
            payload = json.loads(event.payload_json)
            assert payload["coding"]["last_coded_event_seq"] == payload["_rollback_snapshot"]["simulation_state"]["dynamic_state"]["last_coded_event_seq"]
        assert verify_run_audit(db, run_id).verified
        # Resume a pending checkpoint: the old accepted event must be coded, not skipped as "already in the list".
        snapshot = json.loads(events[0].payload_json)["_rollback_snapshot"]
        branch = asyncio.run(WorkflowEngine(Provider()).run(db, workflow(), RunRequest(input={"_rollback": {"continuous_snapshot": snapshot}})))
        branch_run = db.get(RunRecord, branch)
        branch_output = json.loads(branch_run.output_json)
        assert branch_run.status == ("succeeded" if repaired else "failed")
        assert len(branch_output["actions"]) == 2
        if repaired:
            assert branch_output["simulation"]["dynamic_state"]["values"]["position"]["source_refs"][0]["event_seq"] == accepted_seq
    database.dispose()
