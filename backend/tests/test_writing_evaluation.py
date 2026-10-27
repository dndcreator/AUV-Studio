from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from app.eval_runner import _execute_writing, main
from app.evaluation.budget import BudgetedProvider, EvalBudget
from app.evaluation.scenario import load_scenario
from app.evaluation.writing import benchmark_identity, judge_samples, load_styles
from app.narrative import narrative_prompts


class Provider:
    default_model = "fake-writer"
    max_output_tokens = None

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        self.prompts.append(user_prompt)
        if "literary fidelity checker" in system_prompt:
            content = '{"decision":"accept","findings":[]}'
        elif "fixed criteria" in system_prompt:
            content = json.dumps({"samples": [{"style": name, "scores": {
                "fidelity": 4, "readability": 3, "economy": 3, "form": 4, "style": 3,
            }, "issues": []} for name in ("novel", "screenplay")]})
        else:
            content = "雨棚下，她把书递过去。" if "克制的现实主义" in user_prompt else "外景 书店门口 傍晚\n林夏递书。\n周远：谢谢。"
        return {"content": content, "raw": {"usage": {"prompt_tokens": 100, "completion_tokens": 40}}}


def test_fixed_forms_share_facts_but_have_separate_writing_requirements() -> None:
    profiles = load_styles(["novel", "screenplay"])
    assert profiles[0]["criteria"] != profiles[1]["criteria"]
    for profile in profiles:
        system, user = narrative_prompts(source="The same source.", style=profile["prompt"], language="Chinese", audience="general", length="short")
        assert profile["prompt"] in user
        assert "The same source." in user
        assert "user's requested form and style take priority" in system
        assert "no minimum word count" in user


def test_fantasy_ending_uses_explicit_cultural_style_and_requires_completion():
    scenario, workflow = load_scenario("fantasy_revenge_ending")
    assert workflow.nodes == []
    assert scenario["max_agents"] == 2
    assert scenario["run_limits"] == {"rounds": 3, "actions": 6, "output_tokens": 1600}
    assert scenario["expected"]["require_director_completion"] is True
    profile = load_styles(scenario["styles"])[0]
    assert profile["id"] == "fantasy_tragedy"
    assert "西方奇幻" in profile["prompt"]
    assert "不能补写" in profile["prompt"]


def test_fantasy_ending_preflight_stays_within_authorized_calls(capsys):
    assert main(["--scenario", "fantasy_revenge_ending", "--max-calls", "24",
                 "--max-total-tokens", "60000", "--max-output-tokens", "3200", "--max-cost-usd", "0.10"]) == 0
    output = capsys.readouterr().out
    assert "about 22" in output
    assert "Preflight only" in output


def test_writing_benchmark_uses_five_calls_and_one_overwritten_record(monkeypatch) -> None:
    import app.eval_runner as runner
    result_dir = Path("data") / f"writing-eval-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(runner, "RESULTS_DIR", result_dir)
    scenario, _ = load_scenario("writing_styles")
    profiles = load_styles(scenario["styles"])
    try:
        for _ in range(2):
            inner = Provider()
            provider = BudgetedProvider(inner, EvalBudget(max_calls=5, max_total_tokens=12000, max_cost_usd=0.1))
            artifact, path = asyncio.run(_execute_writing("writing_styles", scenario, provider, provider.budget, True, profiles))
            assert artifact["usage"]["calls"] == 5
            assert all(scenario["writing_source"] in prompt or
                       json.loads(prompt).get("source") == scenario["writing_source"] for prompt in inner.prompts)
            assert [item["score"] for item in artifact["llm_judge"]["samples"]] == [85, 85]
        assert list(result_dir.iterdir()) == [path]
        record = path.read_text(encoding="utf-8")
        assert record.count("雨棚下，她把书递过去。") == 1
        assert "change +0" in record
        assert "Full Log" not in record
        assert runner._latest_result("writing_styles")["benchmark"] == artifact["benchmark"]
        changed = deepcopy(profiles)
        changed[0]["criteria"]["style"] = "Changed standard."
        provider = BudgetedProvider(Provider(), EvalBudget(max_calls=5, max_total_tokens=12000, max_cost_usd=0.1))
        _, path = asyncio.run(_execute_writing("writing_styles", scenario, provider, provider.budget, True, changed))
        assert "change +" not in path.read_text(encoding="utf-8")
    finally:
        if result_dir.is_dir():
            for path in result_dir.iterdir():
                if path.is_file():
                    path.unlink()
            result_dir.rmdir()


def test_benchmark_identity_changes_with_source_requirements_and_criteria() -> None:
    profiles = load_styles(["novel", "screenplay"])
    baseline = benchmark_identity("source", profiles)
    assert baseline == benchmark_identity("source", load_styles(["novel", "screenplay"]))
    assert baseline != benchmark_identity("new source", profiles)
    for field in ("prompt", "criteria", "version"):
        changed = deepcopy(profiles)
        changed[0][field] = {"form": "changed", "style": "changed"} if field == "criteria" else 2 if field == "version" else "changed"
        assert baseline != benchmark_identity("source", changed)


def test_missing_profile_scores_are_not_filled_in_or_awarded_a_pass() -> None:
    class Incomplete(Provider):
        async def chat(self, **kwargs) -> dict:
            return {"content": '{"samples":[{"style":"novel","scores":{"fidelity":4}}]}'}
    result = asyncio.run(judge_samples(Incomplete(), "source", [], load_styles(["novel", "screenplay"])))
    assert all(item["score"] is None and item.get("error") for item in result["samples"])


@pytest.mark.parametrize("rows", [
    None,
    {},
    [{"style": "novel", "scores": None}],
    [{"style": "novel", "scores": {key: True for key in ("fidelity", "readability", "economy", "form", "style")}}],
    [{"style": "novel", "scores": {key: 5 for key in ("fidelity", "readability", "economy", "form", "style")}}],
    [{"style": "novel", "scores": {key: 3.0 for key in ("fidelity", "readability", "economy", "form", "style")}}],
    [{"style": "novel", "scores": {key: 4 for key in ("fidelity", "readability", "economy", "form", "style")}}] * 2,
])
def test_malformed_or_duplicate_judge_scores_remain_unavailable(rows) -> None:
    class Malformed(Provider):
        async def chat(self, **kwargs) -> dict:
            return {"content": json.dumps({"samples": rows})}
    result = asyncio.run(judge_samples(Malformed(), "source", [], load_styles(["novel"])))
    assert result["samples"][0]["score"] is None


def test_writing_benchmark_preflight_does_not_call_a_model(capsys) -> None:
    assert main(["--scenario", "writing_styles", "--judge", "--max-calls", "5"]) == 0
    assert "about 5" in capsys.readouterr().out
