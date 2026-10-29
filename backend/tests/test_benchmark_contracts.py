from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import httpx
import pytest

from app import behavior_eval
from app.evaluation.acceptance import acceptance, acceptance_exit, compare_baseline
from app.evaluation.benchmarks import calibration_report, calibration_status, load_cases, summarize
from app.evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from app.evaluation.judging import RUBRICS, build_judge_provider, judge_evidence, parse_judgment
from app.evaluation.long_horizon import build_long_horizon_checkpoints, evaluate_long_horizon
from app.evaluation.targets import run_target
from app.evaluation.judge_evidence import target_evidence
from app.long_horizon_eval import _load_scenario


def successful_artifact():
    return {"run": {"status": "succeeded"}, "deterministic_evaluation": {"score": 100, "checks": {"valid": True}}}


@pytest.mark.parametrize("patch,code", [
    ({}, 3), ({"llm_judge": {"error": "invalid response"}}, 3),
    ({"llm_judge": {"samples": [{"score": 0}]}}, 1),
    ({"llm_judge": {"samples": [{"score": None}]}}, 3),
    ({"manual_review": {"status": "fail"}}, 1), ({"manual_review": {"status": "pass"}}, 0),
    ({"llm_judge": {"verdict": "pass"}, "judge_calibration": "not_calibrated"}, 3),
    ({"llm_judge": {"verdict": "pass"}, "deterministic_evaluation": {"checks": {"privacy": False}}}, 1),
    ({"llm_judge": {"verdict": "pass"}, "narratives": [{"error": "adaptation failed"}]}, 1),
])
def test_quality_gate_never_confuses_execution_success_with_acceptance(patch, code):
    artifact = {**successful_artifact(), **patch}
    assert acceptance_exit(artifact) == code
    assert acceptance(artifact)["execution"] == "pass"


def judgment(capability="memory", score=4):
    return {"verdict": "uncertain" if score is None or score == 2 else "fail" if score <= 1 else "pass",
            "dimensions": {key: {"score": score, "reason": "Observed evidence.", "evidence_ids": ["task", "output"]} for key in RUBRICS[capability]}}


@pytest.mark.parametrize("score,verdict", [(0, "fail"), (1, "fail"), (2, "uncertain"), (3, "pass"), (4, "pass"), (None, "uncertain")])
def test_judge_scores_have_fixed_anchors_and_allow_uncertainty(score, verdict):
    result = parse_judgment(json.dumps(judgment(score=score)), "memory", {"task", "output"})
    assert result["verdict"] == verdict


@pytest.mark.parametrize("bad", [
    {}, {"verdict": "pass", "dimensions": {}},
    {**judgment(), "verdict": "fail"},
    {"verdict": "pass", "dimensions": {key: {"score": True, "reason": "x", "evidence_ids": ["task"]} for key in RUBRICS["memory"]}},
    {"verdict": "pass", "dimensions": {key: {"score": 4, "reason": "x", "evidence_ids": ["fabricated"]} for key in RUBRICS["memory"]}},
    {"verdict": "pass", "dimensions": {key: {"score": 4, "reason": "", "evidence_ids": ["task"]} for key in RUBRICS["memory"]}},
])
def test_judge_malformed_or_ungrounded_results_cannot_pass(bad):
    with pytest.raises(ValueError):
        parse_judgment(json.dumps(bad), "memory", {"task", "output"})


@pytest.mark.parametrize("raw", ['{"verdict":"pass","verdict":"fail"}', '{"score":NaN}', '[]', 'not-json'])
def test_judge_parser_rejects_duplicate_nonfinite_and_nonobject_output(raw):
    with pytest.raises(ValueError):
        parse_judgment(raw, "memory", {"task", "output"})


def test_keyword_stuffing_cannot_certify_long_horizon_quality():
    scenario = _load_scenario()
    checkpoints = build_long_horizon_checkpoints(scenario)
    responses = {row["id"]: json.dumps({"decision": "Ignore every old commitment.", "rationale": "I forgot " + ", ".join(row["required_terms"])}) for row in checkpoints}
    result = evaluate_long_horizon(scenario, checkpoints, responses)
    assert all(row["lexical_diagnostics"]["required_terms_present"] for row in result["checkpoints"])
    assert result["semantic_status"] == "review_required"
    assert result["scope"] == "scripted_sparse_checkpoint_recall"


def test_cases_cover_all_capabilities_and_gold_labels_are_not_claimed_human_reviewed():
    _, cases = load_cases()
    gold, standards = load_cases(True)
    assert {row["capability"] for row in cases} == set(RUBRICS)
    for capability in RUBRICS:
        assert {"pass", "fail"}.issubset({row["label"] for row in standards if row["capability"] == capability})
    assert gold["review_status"] == "pending_human" and not gold["reviewed_by"]
    assert calibration_status(gold, None, {}, "actor") == "provisional"


def test_calibration_reports_false_accepts_false_rejects_and_unknowns():
    gold = {"cases": [{"id": "bad", "label": "fail"}, {"id": "good", "label": "pass"}, {"id": "unknown", "label": "uncertain"}]}
    rows = [{"case_id": "bad", "status": "pass"}, {"case_id": "good", "status": "fail"}, {"case_id": "unknown", "status": "uncertain"}]
    report = calibration_report(rows, gold)
    assert report["false_accept_rate"] == report["false_reject_rate"] == 1
    assert report["agreement_rate"] == pytest.approx(1 / 3)


def test_calibration_requires_matching_model_contract_freshness_and_reviewed_examples():
    dataset = {"review_status": "approved", "reviewed_by": "test-only-reviewed-fixture",
               "cases": [{"id": "bad", "label": "fail"}, {"id": "good", "label": "pass"}]}
    record = {"judge_identity": {"model": "judge-v1"}, "calibrations": {"actor": {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "samples": [{"case_id": "bad", "status": "fail"}, {"case_id": "good", "status": "pass"}]}}}
    assert calibration_status(dataset, record, {"model": "judge-v1"}, "actor") == "calibrated"
    assert calibration_status(dataset, record, {"model": "judge-v2"}, "actor") == "uncalibrated"
    stale = {**record, "created_at": datetime.now(timezone.utc).isoformat(), "calibrations": {
        "actor": {**record["calibrations"]["actor"], "created_at": "2020-01-01T00:00:00+00:00"}}}
    assert calibration_status(dataset, stale, record["judge_identity"], "actor") == "stale"
    assert calibration_status(dataset, record, record["judge_identity"], "memory") == "uncalibrated"


def test_independent_judge_receives_only_evidence_not_gold_labels_or_actor_self_score():
    class Judge:
        async def chat(self, **kwargs):
            assert "independent evidence-based" in kwargs["system_prompt"]
            assert '"label"' not in kwargs["user_prompt"]
            return {"content": json.dumps(judgment())}

    result = asyncio.run(judge_evidence(Judge(), "memory", {"task": "A prior pledge", "output": "An observable decision"}))
    assert result["verdict"] == "pass"


def test_judge_has_explicit_configuration_and_never_reuses_actor_secret(monkeypatch):
    for key in ("BASE_URL", "MODEL", "API_KEY", "PROVIDER"):
        monkeypatch.delenv("AUV_EVAL_JUDGE_" + key, raising=False)
    monkeypatch.setenv("AUV_OPENAI_API_KEY", "ACTOR-PRIVATE")
    with pytest.raises(ValueError, match="no actor-key fallback"):
        build_judge_provider()
    monkeypatch.setenv("AUV_EVAL_JUDGE_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("AUV_EVAL_JUDGE_MODEL", "local-judge")
    assert build_judge_provider().api_key == ""


def test_judge_and_target_share_one_budget_and_cancellation_reserves_cost():
    class Provider:
        async def chat(self, **kwargs):
            return {"content": "ok", "raw": {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}}

    actor = BudgetedProvider(Provider(), EvalBudget(max_calls=1, max_cost_usd=1))
    judge = actor.with_provider(Provider())
    asyncio.run(actor.chat("", "actor", "first"))
    with pytest.raises(EvalBudgetExceeded):
        asyncio.run(judge.chat("", "judge", "second"))
    assert judge.usage is actor.usage and actor.usage.calls == 1

    class Slow:
        async def chat(self, **kwargs):
            await asyncio.sleep(10)

    limited = BudgetedProvider(Slow(), EvalBudget(max_calls=2, max_cost_usd=1, max_output_tokens_per_call=80))
    async def timeout():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(limited.chat("", "actor", "question"), timeout=0.01)
    asyncio.run(timeout())
    assert limited.usage.calls == 1 and limited.usage.output_tokens == 80


@pytest.mark.parametrize("field", ["max_cost_usd", "input_usd_per_million", "output_usd_per_million"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_nonfinite_prices_cannot_disable_budget_safeguards(field, bad):
    with pytest.raises(ValueError):
        EvalBudget(**{field: bad})


def test_repeat_summary_and_baseline_comparisons_do_not_overclaim_single_runs():
    rows = [{"case_id": "a", "status": "pass"}, {"case_id": "a", "status": "fail", "failure_type": "semantic_failure"},
            {"case_id": "b", "status": "not_run"}]
    summary = summarize(rows)
    assert summary["pass_rate"] == pytest.approx(1 / 3)
    assert summary["observed_samples"] == 2 and summary["unstable_cases"] == ["a"]
    record = {"benchmark": {"case": "v1"}, "summary": {"trials": 1}}
    assert compare_baseline(record, record)["status"] == "insufficient_repeats"
    other = {**record, "benchmark": {"case": "v2"}}
    assert compare_baseline(record, other)["status"] == "incomparable"
    incomplete = {**record, "summary": {"trials": 2}}
    assert compare_baseline(incomplete, incomplete)["status"] == "incomplete_coverage"
    repeated = {**record, "summary": {"trials": 2, "pass_rate": 0.5, "requested_samples": 2, "observed_samples": 2}}
    assert compare_baseline(repeated, {**repeated, "implementation": "new-code"})["status"] == "comparable"


def test_preflight_never_loads_model_or_judge_and_requires_explicit_paid_selection(monkeypatch, capsys):
    monkeypatch.setattr(behavior_eval, "build_judge_provider", lambda: pytest.fail("preflight must not load credentials"))
    assert behavior_eval.main(["--case", "actor_secret", "--judge", "--repeat", "2"]) == 0
    assert "Preflight only" in capsys.readouterr().out
    assert behavior_eval.main(["--execute"]) == 2
    assert behavior_eval.main(["--case", "missing"]) == 2


def test_budget_exhaustion_marks_remaining_trials_unrun_instead_of_averaging_them_away(monkeypatch):
    async def target(case, provider):
        await provider.chat("", "actor", "one target call")
        return {"action": "A result"}

    class Provider:
        async def chat(self, **kwargs):
            return {"content": "ok"}

    monkeypatch.setattr(behavior_eval, "run_target", target)
    actor = BudgetedProvider(Provider(), EvalBudget(max_calls=1, max_cost_usd=1))
    judge = actor.with_provider(Provider())
    rows = asyncio.run(behavior_eval.execute_cases([{"id": "a", "capability": "actor", "input": {}, "requirements": "scope"}],
        actor, judge, repeat=2, timeout_seconds=5))
    assert [row["status"] for row in rows] == ["error", "not_run"]
    assert rows[0]["failure_type"] == "budget" and rows[0]["usage"]["calls"] == 1


@pytest.mark.parametrize("capability", ["planning", "actor", "action_check", "director", "state_coder", "memory"])
def test_component_benchmarks_call_actual_module_protocols_without_network(capability):
    _, cases = load_cases()
    case = next(row for row in cases if row["capability"] == capability)

    class Provider:
        calls = 0
        async def chat(self, *args, **kwargs):
            self.calls += 1
            output = {"planning": {"goal": "A bounded task", "roles": [{"name": "A", "objective": "Act independently"}]},
                      "actor": {"participation": "act", "action": "I ask a grounded question."},
                      "action_check": {"decision": "accept", "reason": "Offline protocol fixture only", "scope": {"self_owned": True, "public_only": True}, "findings": []},
                      "director": {"decision": "continue", "round_progress": [{"round": 1, "stagnant": False, "goal_progress": "none", "goal_reason": "New words only"}]},
                      "state_coder": {"schema_ops": [], "state_ops": []}, "memory": {"decision": "wait", "rationale": "Read supplied memory"}}[capability]
            return {"content": json.dumps(output)}

    provider = Provider()
    output = asyncio.run(run_target(case, provider))
    assert output and provider.calls == 1


def test_offline_http_guard_allows_mock_transport_but_rejects_real_external_requests():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))) as client:
        assert client.get("https://model.invalid/v1").json()["ok"]
    with httpx.Client(trust_env=False) as client, pytest.raises(AssertionError, match="Offline tests"):
        client.get("https://model.invalid/v1")
    with httpx.Client(trust_env=False) as client, pytest.raises(AssertionError, match="Offline tests"):
        client.get("http://127.0.0.1:1/v1/chat/completions")


def test_production_memory_target_filters_peer_lore_and_history_before_the_model_sees_them():
    _, cases = load_cases()
    case = next(row for row in cases if row["id"] == "memory_corrected_belief")
    class Provider:
        async def chat(self, *args):
            assert "PRIVATE-HISTORY-K7" not in args[2] and "PRIVATE-LORE-K9" not in args[2]
            assert "Blue Dock" in args[2] and "Red Dock" in args[2]
            return {"content": '{"decision":"propose Blue Dock","rationale":"The newer sourced correction supersedes the old note."}'}
    output = asyncio.run(run_target(case, Provider()))
    assert [row["id"] for row in output["checkpoint"]["memory"]] == [1, 2]


def test_system_judge_gets_exact_event_ids_without_a_duplicate_action_log():
    output = {"task": "An endpoint", "roles": {"a": {}}, "status": "succeeded", "audits": [],
              "output": {"actions": [{"event_seq": 7, "action": "UNIQUE_ACTION"}], "completion": {"status": "completed"}}}
    evidence = target_evidence({"capability": "system", "input": {}}, output)
    assert evidence["event.7"]["action"] == "UNIQUE_ACTION"
    assert json.dumps(evidence).count("UNIQUE_ACTION") == 1
    assert "actions" in output["output"]


def test_behavior_cli_uses_real_orchestration_and_one_result_without_promoting_provisional_scores(tmp_path, monkeypatch):
    from app.evaluation.benchmarks import ASSETS
    class Provider:
        api_key = ""
        base_url = "http://127.0.0.1:1/v1"
        default_model = "offline-target"
        provider_kind = "openai_compatible"
        calls = 0
        async def chat(self, model, system_prompt, user_prompt):
            self.calls += 1
            content = judgment("actor") if "independent evidence-based" in system_prompt else {"participation": "act", "action": "I ask for a response."}
            return {"content": json.dumps(content), "raw": {"usage": {"prompt_tokens": 20, "completion_tokens": 10}}}
    actor, judge = Provider(), Provider()
    monkeypatch.setattr("app.execution.provider.build_provider", lambda: actor)
    monkeypatch.setattr("app.model_config_service.apply_stored_model_config", lambda *_: None)
    monkeypatch.setattr(behavior_eval, "build_judge_provider", lambda: judge)
    monkeypatch.setattr(behavior_eval, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(behavior_eval, "ASSETS", tmp_path)
    args = ["--case", "actor_secret", "--repeat", "2", "--judge", "--execute", "--max-calls", "8", "--promote-baseline", "trial-v1"]
    assert behavior_eval.main(args) == 3  # Protocol works, but pending human calibration is not a quality pass.
    result_path = tmp_path / "results" / "behavior_latest.json"
    artifact = json.loads(result_path.read_text(encoding="utf-8"))
    assert artifact["usage"]["calls"] == 4 and actor.calls == judge.calls == 2
    assert len(artifact["requests"]) == 4 and all(row.get("output_digest") for row in artifact["requests"])
    assert "I ask for a response" not in json.dumps(artifact["requests"])
    assert len(list((tmp_path / "results").iterdir())) == 1
    assert artifact["calibration_status"]["actor"] == "provisional"
    assert artifact["summary"]["trials"] == 2
    baseline = json.loads((tmp_path / "baselines" / "trial-v1.json").read_text(encoding="utf-8"))
    assert "samples" not in baseline and baseline["calibration_status"]["actor"] == "provisional"
    assert behavior_eval.main(args) == 2
    assert actor.calls == judge.calls == 2  # Existing baseline rejected BEFORE spending another request.
    assert ASSETS != tmp_path


def test_module_calibration_reuses_other_capabilities_without_refreshing_their_age(tmp_path, monkeypatch):
    class Judge:
        default_model = "offline-judge"
        provider_kind = "openai_compatible"
        base_url = "http://127.0.0.1:1/v1"

        def __init__(self, capability, scores):
            self.capability, self.scores = capability, iter(scores)

        async def chat(self, **kwargs):
            return {"content": json.dumps(judgment(self.capability, next(self.scores)))}

    monkeypatch.setattr(behavior_eval, "RESULTS", tmp_path)
    monkeypatch.setattr(behavior_eval, "run_target", lambda *_: pytest.fail("calibration must never execute an Actor"))
    monkeypatch.setattr(behavior_eval, "build_judge_provider", lambda: Judge("actor", [4, 0, None]))
    assert behavior_eval.main(["--calibrate", "--capability", "actor", "--execute"]) == 3
    first = json.loads((tmp_path / "calibration_latest.json").read_text(encoding="utf-8"))
    assert first["calibration_report"]["agreement_rate"] == 1
    monkeypatch.setattr(behavior_eval, "build_judge_provider", lambda: Judge("memory", [0, 4]))
    assert behavior_eval.main(["--calibrate", "--capability", "memory", "--execute"]) == 3
    second = json.loads((tmp_path / "calibration_latest.json").read_text(encoding="utf-8"))
    assert second["calibrations"]["actor"] == first["calibrations"]["actor"]
    assert second["calibration_report"]["agreement_rate"] == 1
    assert set(second["calibrations"]) == {"actor", "memory"}
    assert len(list(tmp_path.iterdir())) == 1
