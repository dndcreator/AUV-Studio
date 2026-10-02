from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from app.eval_runner import _execute, _safe_base_url, _validate_scenario_safety, main
from app.evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from app.evaluation.quality import evaluate_run
from app.evaluation.scenario import list_scenarios, load_scenario
from app.execution.simulation_loop import is_entity_node


class FakeProvider:
    def __init__(self) -> None:
        self.max_output_tokens: int | None = None

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        return {
            "content": "a concrete answer",
            "raw": {"usage": {"prompt_tokens": 20, "completion_tokens": 5}},
        }


class FailingProvider:
    max_output_tokens: int | None = None

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        raise RuntimeError("provider failed after accepting request")


def test_fixed_scenario_loads_and_has_hard_runtime_limits() -> None:
    raw, workflow = load_scenario("roleplay_basic")
    simulation = workflow.environment.simulation
    assert "roleplay_basic" in list_scenarios()
    assert raw["expected"]["min_actions"] == 3
    assert simulation["horizon_rounds"] == 3
    assert simulation["max_actions"] == 4
    assert [node.id for node in workflow.nodes if is_entity_node(node)] == ["lin", "chen"]


def test_budgeted_provider_stops_before_excess_call() -> None:
    inner = FakeProvider()
    provider = BudgetedProvider(
        inner,
        EvalBudget(max_calls=1, max_total_tokens=1000, max_output_tokens_per_call=100, max_cost_usd=1),
    )
    asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="first"))
    with pytest.raises(EvalBudgetExceeded, match="call budget"):
        asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="second"))
    assert provider.usage.calls == 1
    assert provider.usage.input_tokens == 20
    assert inner.max_output_tokens == 100


def test_budgeted_provider_conservatively_counts_failed_request() -> None:
    provider = BudgetedProvider(
        FailingProvider(),
        EvalBudget(max_calls=2, max_total_tokens=1000, max_output_tokens_per_call=120, max_cost_usd=1),
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="request"))
    assert provider.usage.calls == 1
    assert provider.usage.input_tokens > 0
    assert provider.usage.output_tokens == 120


def test_deterministic_quality_checks_action_contract_and_participation() -> None:
    detail = {
        "status": "succeeded",
        "node_runs": [
            {"node_id": "lin", "status": "succeeded", "output": {"action": "Lin rejects the launch.", "proposal_summary": "delay", "public_state_update": "Launch challenged"}},
            {"node_id": "chen", "status": "succeeded", "output": {"action": "Chen proposes a limited rollout.", "proposal_summary": "limit scope", "public_state_update": "Compromise proposed"}},
            {"node_id": "lin", "status": "succeeded", "output": {"action": "Lin accepts after assigning a check.", "proposal_summary": "verify", "public_state_update": "Check assigned"}},
        ],
    }
    result = evaluate_run(
        detail,
        {"actor_node_ids": ["lin", "chen"], "min_actions": 3, "min_active_actors": 2, "min_unique_action_ratio": 0.75},
    )
    assert result["score"] == 100
    assert result["stats"]["action_count"] == 3


def test_deterministic_quality_detects_unfinished_director_boundary() -> None:
    detail = {
        "status": "succeeded",
        "node_runs": [
            {"node_id": "lin", "status": "succeeded", "output": {"action": "Lin proposes a delay.", "proposal_summary": "delay", "public_state_update": "Delay proposed"}},
            {"node_id": "chen", "status": "succeeded", "output": {"action": "Chen counters with a gate.", "proposal_summary": "gate", "public_state_update": "Gate proposed"}},
            {"node_id": "lin", "status": "succeeded", "output": {"action": "Lin amends the gate.", "proposal_summary": "amend", "public_state_update": "Terms remain open"}},
        ],
    }
    events = [{"event": "episode_audited", "payload": {"decision": "continue"}}]
    result = evaluate_run(
        detail,
        {
            "actor_node_ids": ["lin", "chen"],
            "min_actions": 3,
            "min_active_actors": 2,
            "min_unique_action_ratio": 0.75,
            "require_director_completion": True,
        },
        events,
    )
    assert result["checks"]["director_confirmed_completion"] is False
    assert result["score"] == 83.3
    assert "before the Director confirmed" in result["findings"][0]


def test_cli_defaults_to_preflight_without_model_calls(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--scenario", "roleplay_basic"]) == 0
    output = capsys.readouterr().out
    assert "Preflight only" in output
    assert "hard limit" in output


def test_cli_rejects_unsafe_budget(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--max-cost-usd", "50"]) == 2
    assert "between 0.01 and 5.00" in capsys.readouterr().err


def test_eval_safety_rejects_side_effecting_nodes() -> None:
    _, workflow = load_scenario("roleplay_basic")
    workflow.nodes[1].type = "external_agent"
    with pytest.raises(ValueError, match="side-effecting"):
        _validate_scenario_safety(workflow)


def test_eval_artifact_endpoint_is_sanitized() -> None:
    assert _safe_base_url("https://user:secret@example.com/v1?token=hidden#fragment") == "https://example.com/v1"


def test_eval_executes_isolated_end_to_end_with_fake_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.eval_runner as runner

    class Provider:
        base_url = "http://127.0.0.1:1234/v1"
        api_key = ""
        provider_kind = "openai_compatible"
        default_model = "eval-fake"
        retry_count = 2
        max_output_tokens: int | None = None

        def __init__(self) -> None:
            self.action_index = 0

        async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
            if "route attention" in system_prompt:
                content = '{"active_node_ids":["lin","chen"],"reason":"both own the conflict"}'
            elif "continuity auditor" in system_prompt:
                content = '{"decision":"stop","guidance":"preserve established facts","reason":"a concrete state exists","shared_state_summary":"A limited launch and verification check are agreed."}'
            elif "Record only the concrete outcome" in system_prompt:
                content = "They agree to a limited launch after an assigned verification check."
            else:
                self.action_index += 1
                actor = "Lin" if "Lin Ya" in user_prompt else "Chen"
                content = (
                    '{"participation":"act","action":"'
                    + actor
                    + f' makes concrete move {self.action_index}.","proposal_summary":"advance negotiation",'
                    + '"public_state_update":"The negotiation gains a concrete condition.",'
                    + '"private_state_update":"resolve changes","boundary_signal":"ready"}'
                )
            return {"content": content, "raw": {"usage": {"prompt_tokens": 80, "completion_tokens": 30}}}

    class ConfigDb:
        def close(self) -> None:
            return None

    result_dir = Path("data") / f"eval-test-{uuid.uuid4().hex}"
    try:
        monkeypatch.setattr(runner, "build_provider", Provider)
        monkeypatch.setattr(runner, "SessionLocal", lambda: ConfigDb())
        monkeypatch.setattr(runner, "apply_stored_model_config", lambda db, provider: None)
        monkeypatch.setattr(runner, "RESULTS_DIR", result_dir)
        scenario, workflow = load_scenario("roleplay_basic")
        artifact, json_path, markdown_path = asyncio.run(
            _execute(
                "roleplay_basic",
                scenario,
                workflow,
                EvalBudget(max_calls=12, max_total_tokens=20_000, max_output_tokens_per_call=500, max_cost_usd=1),
                False,
            )
        )
        assert artifact["run"]["status"] == "succeeded"
        assert artifact["deterministic_evaluation"]["score"] == 100
        assert artifact["usage"]["calls"] <= 12
        assert json_path.is_file()
        assert "Manual Review" in markdown_path.read_text(encoding="utf-8")
    finally:
        if result_dir.is_dir():
            for path in result_dir.iterdir():
                if path.is_file():
                    path.unlink()
            result_dir.rmdir()
