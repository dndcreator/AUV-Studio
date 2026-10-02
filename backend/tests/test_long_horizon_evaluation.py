from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from app.evaluation.budget import EvalBudget
from app.evaluation.long_horizon import build_long_horizon_checkpoints, evaluate_long_horizon
from app.long_horizon_eval import _load_scenario, _run_real, main


def test_long_horizon_simulation_preserves_memory_and_worldbook_privacy() -> None:
    scenario = _load_scenario()
    checkpoints = build_long_horizon_checkpoints(scenario)
    evaluation = evaluate_long_horizon(scenario, checkpoints)

    assert [item["turn"] for item in checkpoints] == [20, 40, 60, 60]
    assert evaluation["score"] == 100
    assert evaluation["failures"] == []

    by_id = {item["id"]: item for item in checkpoints}
    ivo_background = {entry["id"] for entry in by_id["ivo_late_route"]["packet"]["background"]["entries"]}
    mara_packet = str(by_id["mara_final_commitment"]["packet"])
    assert {"amber_protocol", "ivo_private_route", "bell_weather"}.issubset(ivo_background)
    assert "ORCHID-17" not in mara_packet
    assert {memory["id"] for memory in by_id["mara_final_commitment"]["packet"]["memory"]} >= {
        "north_gate_pledge",
        "clinic_requirement",
    }


def test_long_horizon_real_response_checks_detect_forgetting_and_leakage() -> None:
    scenario = _load_scenario()
    checkpoints = build_long_horizon_checkpoints(scenario)
    responses = {
        "sen_midpoint": '{"decision":"keep open","rationale":"AMBER-2 applies; the clinic convoy is not confirmed."}',
        "ivo_late_route": '{"decision":"avoid route","rationale":"ORCHID-17 conflicts with BELL-9 during the third storm warning."}',
        "mara_final_commitment": '{"decision":"keep open","rationale":"AMBER-2 applies to the clinic convoy, but I forgot the old pledge."}',
        "crowd_privacy": '{"decision":"react","rationale":"The clinic convoy is pending. ORCHID-17 is also discussed."}',
    }
    evaluation = evaluate_long_horizon(scenario, checkpoints, responses)

    assert evaluation["mode"] == "real"
    assert evaluation["score"] < 100
    assert "mara_final_commitment: required_terms_used" in evaluation["failures"]
    assert "crowd_privacy: forbidden_terms_not_leaked" in evaluation["failures"]


def test_long_horizon_cli_is_preflight_only_by_default(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "Preflight only" in output
    assert "60 turns" in output
    assert "sparse checkpoints: 4" in output


def test_long_horizon_simulation_writes_zero_cost_artifact(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.long_horizon_eval as runner

    result_dir = Path("data") / f"long-eval-{uuid.uuid4().hex}"
    try:
        monkeypatch.setattr(runner, "RESULTS_DIR", result_dir)
        assert main(["--simulate"]) == 0
        assert len(list(result_dir.glob("*.json"))) == 1
        markdown = next(result_dir.glob("*.md")).read_text(encoding="utf-8")
        assert "Mode: `simulated`" in markdown
        assert "Calls: `0`" in markdown
    finally:
        if result_dir.is_dir():
            for path in result_dir.iterdir():
                if path.is_file():
                    path.unlink()
            result_dir.rmdir()


def test_long_horizon_real_mode_uses_only_sparse_budgeted_calls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.long_horizon_eval as runner

    scenario = _load_scenario()
    checkpoints = build_long_horizon_checkpoints(scenario)

    class Provider:
        base_url = "http://127.0.0.1:1234/v1"
        api_key = ""
        provider_kind = "openai_compatible"
        default_model = "fake"
        retry_count = 2
        max_output_tokens: int | None = None

        async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
            checkpoint_id = next(item["id"] for item in checkpoints if item["question"] in user_prompt)
            answers = {
                "sen_midpoint": '{"decision":"keep open","rationale":"AMBER-2 and the clinic convoy prevent closure."}',
                "ivo_late_route": '{"decision":"avoid route","rationale":"ORCHID-17 is unsafe under BELL-9 at the third storm warning."}',
                "mara_final_commitment": '{"decision":"keep open","rationale":"PLEDGE-NORTH, AMBER-2, and the clinic convoy prevent closure."}',
                "crowd_privacy": '{"decision":"wait publicly","rationale":"The clinic convoy remains unconfirmed."}',
            }
            return {"content": answers[checkpoint_id], "raw": {"usage": {"prompt_tokens": 100, "completion_tokens": 20}}}

    class ConfigDb:
        def close(self) -> None:
            return None

    monkeypatch.setattr(runner, "build_provider", Provider)
    monkeypatch.setattr(runner, "SessionLocal", lambda: ConfigDb())
    monkeypatch.setattr(runner, "apply_stored_model_config", lambda db, provider: None)
    artifact = asyncio.run(
        _run_real(
            scenario,
            checkpoints,
            EvalBudget(max_calls=4, max_total_tokens=8_000, max_output_tokens_per_call=300, max_cost_usd=1),
        )
    )
    assert artifact["usage"]["calls"] == 4
    assert artifact["evaluation"]["score"] == 100
