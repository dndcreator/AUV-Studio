import asyncio

import pytest

from app.evaluation.budget import BudgetedProvider, EvalBudget, request_kind
from app.evaluation.profiling import cost_profile


def test_request_cost_and_latency_ranks_are_independent_and_overlap_is_not_double_counted():
    record = {"wall_ms": 100, "requests": [
        {"kind": "actor", "input_tokens": 1000, "output_tokens": 1, "duration_ms": 80, "start_offset_ms": 0},
        {"kind": "action_check", "input_tokens": 10000, "output_tokens": 1, "duration_ms": 20, "start_offset_ms": 0},
    ]}
    report = cost_profile(record)
    assert report["cost_ranking"] == ["action_check", "actor"]
    assert report["latency_ranking"] == ["actor", "action_check"]
    assert report["request_time_ms"] == 100 and report["model_busy_wall_ms"] == 80
    assert report["outside_request_wall_ms"] == 20
    assert report["modules"]["actor"]["p95_ms"] == 80


def test_legacy_aggregates_never_invent_module_latency_or_split_shared_audit():
    record = {"diagnostics": {"usage_by_kind": {"audit": {"calls": 1, "input_tokens": 100, "output_tokens": 10}}},
              "requests": [{"input_tokens": 100, "output_tokens": 10, "duration_ms": 50}]}
    report = cost_profile(record)
    assert report["attribution"] == "legacy_aggregate" and report["latency_ranking"] == []
    assert report["modules"]["audit"]["request_time_ms"] is None
    assert "state_coder" not in report["modules"] and report["unattributed_requests"] == 1


def test_unknown_trace_and_empty_records_do_not_appear_as_a_measured_zero_cost_success():
    report = cost_profile({"requests": [{"input_tokens": 1000}]})
    assert report["attribution"] == "unattributed_trace" and report["estimated_cost_usd"] == .001
    assert report["request_time_ms"] is None
    assert cost_profile({})["attribution"] == "unavailable"


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True])
def test_invalid_rates_are_rejected(bad):
    with pytest.raises(ValueError): cost_profile({}, input_rate=bad)


def test_trace_distinguishes_provider_zero_usage_from_estimates_and_keeps_secrets_out():
    class Provider:
        async def chat(self, **kwargs):
            return {"content": "PRIVATE-OUTPUT", "raw": {"usage": {"prompt_tokens": 0, "completion_tokens": 0}}}
    provider = BudgetedProvider(Provider(), EvalBudget(), capture_trace=True)
    asyncio.run(provider.chat("", "You are a PLAYER OUTCOME STATE CODER", "PRIVATE-INPUT"))
    trace = provider.request_trace[0]
    assert trace["kind"] == "state_coder" and trace["input_tokens"] == trace["output_tokens"] == 0
    assert trace["token_source"] == {"input": "provider", "output": "provider"}
    assert "PRIVATE" not in str(trace)
    assert provider.usage_by_kind["state_coder"]["calls"] == 1


def test_failed_attempt_is_measured_and_conservatively_labelled_reserved():
    class Provider:
        async def chat(self, **kwargs): raise TimeoutError("PRIVATE-ERROR")
    provider = BudgetedProvider(Provider(), EvalBudget(), capture_trace=True)
    with pytest.raises(TimeoutError): asyncio.run(provider.chat("", "Action consistency checker", "private"))
    report = cost_profile({"requests": provider.request_trace})
    row = report["modules"]["action_check"]
    assert row["failed"] == row["reserved_calls"] == 1
    assert "PRIVATE-ERROR" not in str(provider.request_trace)
    assert request_kind("You are a State Coder repairing an uncommitted batch") == "state_repair"


@pytest.mark.parametrize("usage,expected,sources", [
    ({"prompt_tokens": 15, "completion_tokens": 900}, (15, 900), ("provider", "provider")),
    ({"prompt_tokens": 0, "completion_tokens": 0}, (0, 0), ("provider", "provider")),
    ({"completion_tokens": 27}, (None, 27), ("estimate", "provider")),
    ({}, (None, 500), ("estimate", "reserved")),
])
def test_failed_usage_prefers_reported_counters_and_keeps_missing_fields_explicit(usage, expected, sources):
    from app.execution.provider import ProviderError
    class Provider:
        async def chat(self, **kwargs):
            raise ProviderError("PRIVATE-ERROR", code="empty_content", diagnostics={**usage,
                "finish_reason": "length", "reasoning_present": True, "content_chars": 0, "raw": "PRIVATE"})
    provider = BudgetedProvider(Provider(), EvalBudget(), capture_trace=True)
    with pytest.raises(ProviderError): asyncio.run(provider.chat("", "Action consistency checker", "PRIVATE-INPUT"))
    trace = provider.request_trace[0]
    assert trace["error_code"] == "empty_content" and trace["finish_reason"] == "length"
    assert trace["provider_diagnostics"]["reasoning_present"] is True
    assert trace["output_tokens"] == provider.usage.output_tokens == expected[1]
    assert trace["input_tokens"] == provider.usage.input_tokens
    if expected[0] is not None: assert trace["input_tokens"] == expected[0]
    assert trace["token_source"] == dict(zip(("input", "output"), sources))
    assert provider.inflight == {"input_tokens": 0, "output_tokens": 0}
    assert "PRIVATE" not in str(trace)
    assert cost_profile({"requests": [trace]})["modules"]["action_check"]["reserved_calls"] == int(sources[1] == "reserved")


def test_diagnostic_suite_preflights_without_accessing_model_configuration(capsys):
    from app.behavior_eval import main
    assert main(["--suite", "profiling", "--case", "profile_guild_short", "--max-calls", "24"]) == 0
    assert "Preflight only" in capsys.readouterr().out
    assert main(["--suite", "profiling", "--judge"]) == 2


def test_profile_cli_reads_legacy_markdown_without_replacing_evidence(tmp_path):
    import json
    from app.evaluation.profiling import main
    source, output = tmp_path / "source.md", tmp_path / "profile.json"
    text = '<!-- AUV_EVAL {"diagnostics":{"usage_by_kind":{"actor":{"calls":1,"input_tokens":1000}}}} -->\n# Original'
    source.write_text(text, encoding="utf-8")
    assert main(["--record", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["estimated_cost_usd"] == .001
    assert source.read_text() == text
    with pytest.raises(SystemExit): main(["--record", str(source), "--output", str(source)])
