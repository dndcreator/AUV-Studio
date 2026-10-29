from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

from app.behavior_eval import execute_cases
from app import behavior_eval
from app.evaluation.acceptance import compare_baseline
from app.evaluation import benchmarks
from app.evaluation.benchmarks import case_fingerprint, load_cases, summarize
from app.evaluation.budget import BudgetedProvider, EvalBudget
from app.evaluation.targets import run_target
from test_system_journey import ScenarioProvider


def get_case(case_id):
    return next(case for case in load_cases()[1] if case["id"] == case_id)


def test_gold_inventory_covers_five_distinct_risks_per_capability_and_review_is_explicit():
    dataset, cases = load_cases()
    assert dataset["version"] == 2 and len(cases) == 41
    for capability in {case["capability"] for case in cases}:
        selected = [case for case in cases if case["capability"] == capability]
        assert 5 <= len(selected) <= 10
        assert {"positive", "negative", "boundary", "historical"}.issubset({case["kind"] for case in selected})
        assert len({json.dumps(case["input"], sort_keys=True) for case in selected}) == len(selected)
        for case in selected:
            assert case["gold"]["review_status"] == "pending_human"
            assert not case["gold"]["reviewed_by"]
            assert len(case["gold"]["pass_conditions"]) >= 2
            assert len(case["gold"]["fail_conditions"]) >= 2


def test_referenced_scenario_changes_invalidate_case_identity_before_comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmarks, "ASSETS", tmp_path)
    (tmp_path / "scenarios").mkdir()
    fixture = tmp_path / "scenarios" / "example.json"
    case = {"id": "test", "capability": "system", "input": {"scenario": "example"}}
    fixture.write_text('{"task":"original endpoint"}', encoding="utf-8")
    before = case_fingerprint([case])
    fixture.write_text('{"task":"different endpoint"}', encoding="utf-8")
    assert case_fingerprint([case]) != before
    case["input"]["scenario"] = "../example"
    with pytest.raises(ValueError, match="scenario ID"):
        case_fingerprint([case])


def test_equal_aggregate_scores_still_expose_case_regression_and_improvement():
    def record(outcomes):
        rows = [{"case_id": name, "status": status} for name, status in outcomes for _ in range(2)]
        return {"benchmark": {"suite": "fixed"}, "summary": {**summarize(rows), "by_case": {
            name: summarize([row for row in rows if row["case_id"] == name]) for name, _ in outcomes}}}
    result = compare_baseline(record([("privacy", "pass"), ("progress", "fail")]), record([("privacy", "fail"), ("progress", "pass")]))
    assert result["deltas"]["pass_rate"] == 0
    assert result["by_case"]["privacy"]["observed_direction"] == "worse"
    assert result["by_case"]["progress"]["observed_direction"] == "better"


@pytest.mark.parametrize("invalid", ["empty_oracle", "false_approval"])
def test_incomplete_gold_metadata_is_rejected_before_execution(tmp_path, monkeypatch, invalid):
    case = deepcopy(get_case("actor_secret"))
    if invalid == "empty_oracle":
        case["gold"]["pass_conditions"] = []
    else:
        case["gold"].update(review_status="approved", reviewed_by="")
    (tmp_path / "behavior_cases.json").write_text(json.dumps({"version": 2, "cases": [case]}), encoding="utf-8")
    monkeypatch.setattr(benchmarks, "ASSETS", tmp_path)
    with pytest.raises(ValueError, match="Gold case"):
        load_cases()


def test_gold_contrast_is_not_given_to_the_tested_actor():
    case = deepcopy(get_case("actor_secret"))
    case["gold"]["contrast_example"] = "ORACLE-SECRET-CONTRAST-MARKER"
    class Actor:
        async def chat(self, **kwargs):
            assert "ORACLE-SECRET-CONTRAST-MARKER" not in kwargs["user_prompt"]
            return {"content": '{"participation":"wait","action":""}'}
    assert asyncio.run(run_target(case, Actor()))["participation"] == "wait"


class PipelineProvider(ScenarioProvider):
    def __init__(self):
        super().__init__()
        self.systems = []
        self.fail_report = False

    async def chat(self, model, system_prompt, user_prompt):
        self.systems.append(system_prompt)
        if "route attention" in system_prompt:
            self.actors = [row["node_id"] for row in json.loads(user_prompt.split("AVAILABLE NODES:\n", 1)[1])]
        if "literary fidelity checker" in system_prompt:
            return {"content": '{"decision":"accept","findings":[]}'}
        if "finished literary work" in system_prompt or "analyst writing a concise" in system_prompt:
            if self.fail_report:
                raise RuntimeError("offline report failure")
            return {"content": "The participants proposed a route and identified what remains unverified."}
        return await super().chat(model, system_prompt, user_prompt)


@pytest.mark.parametrize("case_id", ["system_guild_to_novel", "system_research_to_brief", "system_handoff_to_brief"])
def test_cross_module_task_calls_real_plan_engine_and_report(case_id):
    # Fake model envelopes test wiring only; they do not certify these tasks' semantic outcomes.
    provider = PipelineProvider()
    output = asyncio.run(run_target(get_case(case_id), provider))
    assert output["status"] == "succeeded" and output["deliverable_status"] == "generated"
    assert output["output"]["actions"] and output["audits"] and output["coding_progress"]
    assert output["report"]["text"]
    assert any("action consistency checker" in system for system in provider.systems)
    assert any("continuity auditor" in system for system in provider.systems)
    if case_id != "system_handoff_to_brief":
        assert any("process architect" in system for system in provider.systems)


def test_incomplete_system_never_generates_deliverable():
    provider = PipelineProvider()
    provider.allow_completion = False
    output = asyncio.run(run_target(get_case("system_guild_to_novel"), provider))
    assert output["status"] == "stopped" and output["deliverable_status"] == "skipped_incomplete"
    assert "report" not in output
    assert not any("finished literary work" in system for system in provider.systems)


def test_report_failure_preserves_actions_and_stops_judge_calls():
    provider = PipelineProvider()
    provider.fail_report = True
    limited = BudgetedProvider(provider, EvalBudget(max_calls=50, max_total_tokens=100000, max_cost_usd=1))
    class Judge:
        async def chat(self, **kwargs):
            pytest.fail("A known report failure must not spend an additional Judge call")
    rows = asyncio.run(execute_cases([get_case("system_guild_to_novel")], limited, limited.with_provider(Judge()), repeat=2, timeout_seconds=20))
    assert [row["status"] for row in rows] == ["error", "not_run"]
    assert rows[0]["stage"] == "deliverable"
    assert rows[0]["output"]["output"]["actions"]
    assert rows[0]["output"]["report_error"]["type"] == "RuntimeError"


def test_simulation_error_cannot_be_downgraded_to_missing_long_horizon_coverage(monkeypatch):
    async def failed_target(*_):
        return {"status": "failed", "task": "A long task", "roles": {}, "audits": [], "observed_rounds": 1,
                "output": {"actions": []}, "error": {"type": "StateCodingError"}}
    class NeverCalled:
        async def chat(self, **kwargs):
            pytest.fail("A failed simulation must not spend another Judge request")
    monkeypatch.setattr(behavior_eval, "run_target", failed_target)
    limited = BudgetedProvider(NeverCalled(), EvalBudget(max_calls=1))
    rows = asyncio.run(execute_cases([get_case("system_long_continuity")], limited, limited, repeat=2, timeout_seconds=5))
    assert [row["status"] for row in rows] == ["error", "not_run"]
    assert rows[0]["failure_type"] == "simulation_error" and limited.usage.calls == 0
