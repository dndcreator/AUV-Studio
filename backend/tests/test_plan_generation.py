from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from app.execution.provider import ProviderError
from app.main import app
from app.plan_compiler import compile_workflow_from_plan
from app.plan_generation import PlanGenerationError, generate_plan_object
from app.schemas import PlanCompileRequest


VALID = {"intent_mode": "roleplay", "goal": "Visit a guild", "roles": [{"name": "Adventurer", "count": 1}, {"name": "Clerk", "count": 1}]}


class Sequence:
    retry_count = 9

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    async def chat(self, model, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def response(content=VALID, finish="stop"):
    return {"content": json.dumps(content) if not isinstance(content, str) else content,
            "raw": {"choices": [{"finish_reason": finish}], "usage": {"prompt_tokens": 20, "completion_tokens": 30}}}


@pytest.mark.parametrize("bad,code", [(response('{"goal":', "length"), "output_truncated"),
    (response("invalid"), "invalid_json"), (response({}), "invalid_plan_schema"),
    (response({**VALID, "roles": [{"name": "A", "count": "bad"}]}), "invalid_plan_schema")])
def test_invalid_plan_regenerates_once_without_applying_a_fallback(bad, code, caplog):
    provider = Sequence(bad, response())
    result = asyncio.run(compile_workflow_from_plan(provider, PlanCompileRequest(plan_text="Visit a fantasy guild.", mode="roleplay")))
    assert len(provider.prompts) == 2
    assert result.generation["attempts"] == 2
    assert result.generation["recovered_errors"][0]["code"] == code
    assert "Regenerate one COMPLETE compact JSON" in provider.prompts[1]
    assert {node.config.get("entity_name") for node in result.workflow.nodes} >= {"Adventurer", "Clerk"}
    assert code in caplog.text


def test_two_bad_outputs_raise_with_finish_reason_and_no_private_text_in_logs(caplog):
    provider = Sequence(response('PRIVATE-PLOT {', "length"), response("PRIVATE-PLOT malformed"))
    with pytest.raises(PlanGenerationError) as exc:
        asyncio.run(generate_plan_object(provider, "architect", "plan"))
    assert len(provider.prompts) == 2
    assert [row["code"] for row in exc.value.diagnostics] == ["output_truncated", "invalid_json"]
    assert exc.value.diagnostics[0]["finish_reason"] == "length"
    assert all(row["response_chars"] > 0 for row in exc.value.diagnostics)
    assert "PRIVATE-PLOT" not in caplog.text


@pytest.mark.parametrize("error", [ProviderError("provider rejected", retryable=False, code="http_error", status_code=402),
    EvalBudgetExceeded("budget reached"), RuntimeError("internal error")])
def test_request_errors_propagate_instead_of_becoming_a_draft(error):
    provider = Sequence(error)
    with pytest.raises(type(error)) as exc:
        asyncio.run(compile_workflow_from_plan(provider, PlanCompileRequest(plan_text="A scenario")))
    assert exc.value is error and len(provider.prompts) == 1


def test_unconfigured_mock_provider_is_not_mistaken_for_invalid_model_json():
    provider = Sequence({"content": "[mock-response]", "raw": {"mock": True}})
    with pytest.raises(ProviderError) as exc:
        asyncio.run(compile_workflow_from_plan(provider, PlanCompileRequest(plan_text="A fantasy opening")))
    assert exc.value.code == "model_not_configured" and not exc.value.retryable
    assert len(provider.prompts) == 1


def test_budget_wrapper_retries_a_transient_request_and_counts_both_attempts(monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("app.evaluation.budget.asyncio.sleep", sleep)
    inner = Sequence(ProviderError("connection failed", code="network_error"), response())
    provider = BudgetedProvider(inner, EvalBudget(max_calls=3, max_total_tokens=10000, max_output_tokens_per_call=100), retry_count=1)
    result = asyncio.run(provider.chat("", "system", "request"))
    assert result["content"] == json.dumps(VALID)
    assert inner.retry_count == 0 and provider.usage.calls == 2
    assert provider.usage.output_tokens == 130
    assert delays == [1] and provider.retries == [{"after_call": 1, "code": "network_error", "status_code": None}]


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
def test_nonrecoverable_requests_are_not_retried(status):
    inner = Sequence(ProviderError("no", retryable=False, code="http_error", status_code=status))
    provider = BudgetedProvider(inner, EvalBudget(), retry_count=2)
    with pytest.raises(ProviderError):
        asyncio.run(provider.chat("", "system", "request"))
    assert provider.usage.calls == 1 and len(inner.prompts) == 1


def test_transient_retries_stop_at_the_same_hard_budget(monkeypatch):
    async def sleep(delay):
        pass

    monkeypatch.setattr("app.evaluation.budget.asyncio.sleep", sleep)
    inner = Sequence(ProviderError("connection failed"), response())
    provider = BudgetedProvider(inner, EvalBudget(max_calls=1), retry_count=1)
    with pytest.raises(EvalBudgetExceeded, match="call budget"):
        asyncio.run(provider.chat("", "system", "request"))
    assert provider.usage.calls == len(inner.prompts) == 1


@pytest.mark.parametrize("failure", ["truncated", "balance"])
def test_browser_api_returns_the_same_failure_code_without_raw_model_or_credentials(monkeypatch, failure):
    provider = (Sequence(response("PRIVATE-PLOT", "length"), response("PRIVATE-PLOT", "length")) if failure == "truncated"
                else Sequence(ProviderError("sensitive provider message", retryable=False, code="http_error", status_code=402)))
    monkeypatch.setattr("app.main.provider", provider)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        result = client.post("/api/templates/compile", json={"plan_text": "A fantasy opening"})
    finally:
        client.close()
    assert result.status_code == 502
    detail = result.json()["detail"]
    assert detail["code"] == ("output_truncated" if failure == "truncated" else "http_error")
    assert "No fallback draft" in detail["message"]
    assert "PRIVATE-PLOT" not in result.text and "sensitive provider message" not in result.text


@pytest.mark.parametrize("valid_plan", [True, False])
def test_natural_language_evaluation_saves_one_compact_result_even_when_compilation_fails(monkeypatch, tmp_path, valid_plan):
    import app.eval_runner as runner
    from app.evaluation.scenario import load_scenario

    class Provider:
        api_key = "offline-fixture"
        base_url = "https://offline.invalid/v1"
        default_model = "offline"
        provider_kind = "openai_compatible"
        max_output_tokens = None
        retry_count = 0
        actor_calls = 0

        async def chat(self, model, system_prompt, user_prompt):
            if "process architect" in system_prompt:
                return response() if valid_plan else response("invalid", "length")
            if "route attention" in system_prompt:
                return {"content": '{"active_node_ids":["agent_1","agent_2"]}'}
            if "action consistency checker" in system_prompt:
                return {"content": '{"decision":"accept","reason":"Offline fixture.","scope":{"self_owned":true,"public_only":true},"findings":[]}'}
            if "continuity auditor" in system_prompt:
                return {"content": '{"decision":"stop","task_complete":true,"completion_reason":"Offline fixture goal reached."}'}
            if "finished literary work" in system_prompt:
                return {"content": "柜台前，新手握着背包带，问起第一份委托。"}
            if "literary fidelity checker" in system_prompt:
                return {"content": '{"decision":"accept","findings":[]}'}
            self.actor_calls += 1
            return {"content": json.dumps({"participation": "act", "action": f"I make proposal {self.actor_calls}.", "intent": "Ask about a task."})}

    monkeypatch.setattr(runner, "build_provider", Provider)
    monkeypatch.setattr(runner, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(runner, "apply_stored_model_config", lambda *_args: None)
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path)
    scenario, workflow = load_scenario("fantasy_opening")
    assert workflow.nodes == []  # No prefilled plot or cast.
    artifact, path = asyncio.run(runner._execute("fantasy_opening", scenario, workflow,
        EvalBudget(max_calls=18, max_total_tokens=100000, max_output_tokens_per_call=3200, max_cost_usd=2), False))
    assert artifact["run"]["status"] == ("succeeded" if valid_plan else "failed")
    assert list(tmp_path.iterdir()) == [path]
    text = path.read_text(encoding="utf-8")
    if valid_plan:
        assert artifact["compilation"]["attempts"] == 1
        assert artifact["narratives"] and "柜台前" in text
        assert artifact["consistency"]["checked"] == 2
    else:
        assert artifact["usage"]["calls"] == 2
        assert artifact["run"]["error"]["code"] == "output_truncated"
        assert "finish_reason" in text and "length" in text


def test_simulation_success_does_not_hide_a_failed_literary_deliverable(monkeypatch):
    import app.eval_runner as runner

    async def execute(*args):
        return {"run": {"status": "succeeded"}, "result_status": "partial",
                "deterministic_evaluation": {"score": 100}, "narratives": [{"style": "novel", "error": "network_error"}],
                "usage": {"calls": 1, "input_tokens": 10, "output_tokens": 10, "estimated_cost_usd": 0}}, "not-written.md"

    monkeypatch.setattr(runner, "_execute", execute)
    assert runner.main(["--scenario", "roleplay_basic", "--execute", "--max-calls", "18"]) == 1
