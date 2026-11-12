import asyncio
import json

import pytest

from app.evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from app.evaluation.parallel_trial import DelayedProvider, MODES, STORY_MODES, campaign, main, packet, parse_joint, run_mode, workflow


def provider(case="independent"):
    return BudgetedProvider(DelayedProvider(case), EvalBudget(max_calls=24, max_total_tokens=80000), capture_trace=True)


@pytest.mark.parametrize("mode", MODES)
def test_independent_admissions_preserve_snapshot_semantics_and_use_expected_call_counts(mode):
    row = asyncio.run(run_mode(provider(), mode))
    assert row["status"] == "completed" and len(row["accepted"]) == 2
    assert row["calls"] == (3 if mode == "parallel_batch" else 4)
    assert bool(row["inputs"][1]["received"]) == (mode == "serial")


@pytest.mark.parametrize("mode", MODES)
def test_competing_resource_never_admits_two_claims_and_no_unknown_priority_is_invented(mode):
    row = asyncio.run(run_mode(provider("contested"), mode, "contested"))
    assert row["status"] == "completed" and len(row["accepted"]) <= 1
    if mode == "parallel_batch": assert not row["accepted"]


@pytest.mark.parametrize("mode", MODES)
def test_question_response_uses_previous_round_for_parallel_and_same_round_for_serial(mode):
    row = asyncio.run(run_mode(provider("reaction"), mode, "reaction"))
    assert row["status"] == "completed"
    first_b = next(item for item in row["assessments"] if item["actor"] == "b")
    assert (first_b["action"] == "The door is locked.") == (mode == "serial")
    assert next(item for item in row["assessments"] if item["actor"] == "b" and item["round"] == 2)["action"] == "The door is locked."


def test_shared_budget_reserves_inflight_requests_across_scoped_copies_and_releases_on_cancel():
    class Waiting:
        async def chat(self, **kwargs): await asyncio.sleep(10)
    async def check():
        wrapped = BudgetedProvider(Waiting(), EvalBudget(max_total_tokens=1000, max_output_tokens_per_call=600), capture_trace=True)
        task = asyncio.create_task(wrapped.chat("", "s", "u"))
        await asyncio.sleep(.01)
        with pytest.raises(EvalBudgetExceeded): await wrapped.with_output_limit(600).chat("", "s", "u")
        assert wrapped.usage.calls == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert sum(wrapped.inflight.values()) == 0
    asyncio.run(check())


def test_joint_checker_cannot_mark_an_incompatible_group_all_accepted_or_omit_an_actor():
    accept = {"decision": "accept", "reason": "fixture", "scope": {"self_owned": True, "public_only": True}, "findings": []}
    body = {"assessments": {"a": accept, "b": accept}, "joint_findings": [{"candidate_ids": ["a", "b"], "reason": "conflict", "evidence_ids": ["rule"]}]}
    with pytest.raises(ValueError): parse_joint({"content": json.dumps(body)}, [{"actor_id": "a"}, {"actor_id": "b"}], [{"id": "rule"}])
    body["joint_findings"] = []; body["assessments"].pop("b")
    with pytest.raises(ValueError): parse_joint({"content": json.dumps(body)}, [{"actor_id": "a"}, {"actor_id": "b"}], [{"id": "rule"}])


def test_preflight_is_zero_call_and_expired_campaign_retains_not_run_modes(capsys):
    assert main([]) == 0
    assert "Zero-call" in capsys.readouterr().out
    rows = asyncio.run(campaign(provider(), ["independent"], 0))
    assert len(rows) == 3 and all(item["status"] == "not_run" for item in rows)


def test_shared_round_version_still_projects_private_state_per_actor():
    from app.execution.simulation_state import init_simulation_state
    wf = workflow("independent"); state = init_simulation_state(wf)
    state["dynamic_state"]["concepts"]["code"] = {"id": "code", "status": "active", "visibility": "private", "owner_id": "a"}
    state["dynamic_state"]["values"]["code"] = {"value": "PRIVATE-CODE"}
    assert "PRIVATE-CODE" in json.dumps(packet(wf, wf.nodes[0], state, [], 1))
    assert "PRIVATE-CODE" not in json.dumps(packet(wf, wf.nodes[1], state, [], 1))


@pytest.mark.parametrize("mode", STORY_MODES)
def test_castle_trial_is_four_rounds_with_private_profiles_and_shared_accepted_history(mode):
    from app.execution.simulation_state import init_simulation_state
    wf = workflow("castle"); state = init_simulation_state(wf)
    a = packet(wf, wf.nodes[0], state, [], 1)
    b = packet(wf, wf.nodes[1], state, [], 1)
    secret = "king ordered Mara's brother's execution"
    assert secret not in json.dumps(a) and secret in json.dumps(b)
    row = asyncio.run(run_mode(provider("castle"), mode, "castle"))
    assert row["status"] == "completed" and len(row["accepted"]) == 8
    assert row["calls"] == (16 if mode == "parallel_propose" else 12)
    assert [item["round"] for item in row["inputs"]] == [1, 1, 2, 2, 3, 3, 4, 4]
    for index in range(0, 8, 2):
        assert row["inputs"][index]["received"] == row["inputs"][index + 1]["received"]
        assert len(row["inputs"][index]["received"]) == index


def test_castle_preflight_and_mode_selection_never_include_serial(capsys):
    assert main(["--case", "castle"]) == 0
    text = capsys.readouterr().out
    assert "40 calls" in text and "Thinking unchanged" in text
    rows = asyncio.run(campaign(provider("castle"), ["castle"], 0, STORY_MODES))
    assert [row["mode"] for row in rows] == list(STORY_MODES)


def test_story_actor_protocol_failure_keeps_field_issues_without_raw_response():
    class BadActor:
        async def chat(self, **kwargs):
            return {"content": '{"participation":"act","action":42,"intent":"PRIVATE"}'}
    wrapped = BudgetedProvider(BadActor(), EvalBudget(), capture_trace=True)
    row = asyncio.run(run_mode(wrapped, "parallel_batch", "castle"))
    assert row["status"] == "error" and row["accepted"] == []
    assert row["error"]["phase"] == "actor_proposals" and row["error"]["round"] == 1
    assert row["error"]["issues"] == [{"field": "action", "type": "string_type"}]
    assert "PRIVATE" not in json.dumps(row) and wrapped.usage.calls == 2


def test_nonthinking_preflight_is_explicit_and_actor_contract_has_typed_example(capsys):
    from app.execution.simulation_state import init_simulation_state
    assert main(["--case", "castle", "--thinking-mode", "disabled"]) == 0
    assert "Thinking disabled" in capsys.readouterr().out
    wf = workflow("castle")
    prompt = packet(wf, wf.nodes[0], init_simulation_state(wf), [], 1)["prompt"]
    assert '{"participation":"wait","action":""}' in prompt
    assert "not a request to wait" in prompt and "never objects, arrays, booleans or null" in prompt
    with pytest.raises(SystemExit): main(["--thinking-mode", "disabled"])
