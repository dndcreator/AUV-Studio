from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

import httpx
import pytest

# Set before test collection imports app.db; a forgotten override must never open the user's database.
_isolated = tempfile.TemporaryDirectory(prefix="auv-test-suite-")
os.environ["AUV_DATABASE_URL"] = "sqlite:///" + (Path(_isolated.name) / "suite.db").as_posix()
os.environ["AUV_MEDIA_OUTPUT_DIR"] = (Path(_isolated.name) / "media").as_posix()

UNIT_FILES = {
    "test_action_consistency.py", "test_action_evidence.py", "test_action_output.py", "test_behavior_validation.py",
    "test_context_book.py", "test_dynamic_state.py", "test_entity_contracts.py", "test_evaluation.py",
    "test_evidence.py", "test_launcher.py", "test_long_horizon_evaluation.py", "test_narrative_fidelity.py",
    "test_perception.py", "test_plan_generation.py", "test_scene_control.py", "test_simulation_contract_repairs.py",
    "test_state_coding.py", "test_writing_evaluation.py", "test_benchmark_contracts.py",
}
INTEGRATION_FILES = {"test_api_integration.py", "test_api_surface.py", "test_engine.py", "test_human_api.py",
                     "test_media.py", "test_provider.py", "test_queue_manager.py", "test_report_material.py", "test_run_recovery.py"}
INTEGRATION_CASES = {
    "test_real_engine_admits_only_checked_actions_and_keeps_rejected_candidates_out_of_memory_and_reports",
    "test_external_agent_conflict_is_not_automatically_reissued",
    "test_http_external_adapter_uses_strict_admission_without_reissuing_requests",
    "test_real_engine_retains_earlier_actions_but_never_publishes_invalid_output",
    "test_real_engine_24_rounds_keeps_authority_privacy_and_rejects_unsupported_changes",
    "test_dynamic_state_checkpoint_restores_exact_private_snapshot",
    "test_continuous_failures_keep_accepted_state_actions_and_rollback_evidence",
    "test_engine_injects_only_node_visible_background_and_traces_activation",
    "test_external_agent_does_not_receive_context_book_by_default",
    "test_continuous_engine_applies_state_patch_inside_existing_director_audit",
    "test_eval_executes_isolated_end_to_end_with_fake_provider",
    "test_real_continuous_runtime_isolates_history_templates_and_owner_recall",
    "test_external_agent_http_envelope_receives_only_delivered_public_information",
    "test_browser_api_returns_the_same_failure_code_without_raw_model_or_credentials",
    "test_natural_language_evaluation_saves_one_compact_result_even_when_compilation_fails",
    "test_simulation_success_does_not_hide_a_failed_literary_deliverable",
    "test_real_http_payloads_have_scoped_planning_limits_and_share_one_budget",
    "test_real_engine_protocol_checks_false_delta_without_extra_calls_and_can_stop_early",
    "test_real_engine_supplies_goal_feedback_and_rolls_back_the_counter_without_extra_calls_or_invented_completion",
    "test_real_engine_canonical_context_and_terminal_semantics",
    "test_real_engine_commits_coding_and_cursor_together_and_fails_closed_after_repair",
    "test_writing_benchmark_uses_five_calls_and_one_overwritten_record",
    "test_behavior_cli_uses_real_orchestration_and_one_result_without_promoting_provisional_scores",
    "test_module_calibration_reuses_other_capabilities_without_refreshing_their_age",
}
UNIT_CASES = {"test_cycle_validation", "test_memory_write_trigger_filters_trivial_facts"}


def pytest_configure(config):
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(_isolated.name) / "pytest")


def pytest_sessionfinish():
    database = sys.modules.get("app.db")
    if database is not None:
        database.engine.dispose()


def pytest_collection_modifyitems(items):
    for item in items:
        filename = item.path.name
        name = item.originalname or item.name.split("[", 1)[0]
        if filename == "test_system_journey.py":
            layer = "system"
        elif name in UNIT_CASES:
            layer = "unit"
        elif name in INTEGRATION_CASES or filename in INTEGRATION_FILES:
            layer = "integration"
        elif filename in UNIT_FILES:
            layer = "unit"
        else:
            raise pytest.UsageError(f"Unclassified test asset: {filename}; update the explicit layer inventory")
        item.add_marker(getattr(pytest.mark, layer))


def pytest_terminal_summary(terminalreporter):
    counts = Counter(next(marker for marker in ("unit", "integration", "system") if item.get_closest_marker(marker))
                     for item in terminalreporter._session.items)
    terminalreporter.write_line("Selected layers: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


@pytest.fixture(autouse=True)
def forbid_paid_http(monkeypatch):
    def check(request):
        raise AssertionError("Offline tests cannot send real HTTP, including local model proxies; use MockTransport/ASGI or the opt-in evaluation CLI")

    sync = httpx.HTTPTransport.handle_request
    asynchronous = httpx.AsyncHTTPTransport.handle_async_request

    def guarded_sync(self, request):
        check(request)
        return sync(self, request)

    async def guarded_async(self, request):
        check(request)
        return await asynchronous(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
