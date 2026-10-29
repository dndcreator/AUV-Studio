from __future__ import annotations

import copy
import json

import pytest

from app.execution.action_consistency import CHECK_SYSTEM_PROMPT, parse_assessment
from app.execution.action_evidence import build_check_prompt, project_evidence, request_metrics, response_usage
from app.execution.events import redact_secrets


def expand(rows):
    restored = {}
    for raw in rows:
        row = copy.deepcopy(raw)
        for alias in row.pop("refs", []):
            assert alias["source"] in restored  # No forward references or cycles.
            value = restored[alias["source"]]["value"]
            for key in alias["source_path"]:
                value = value[key]
            path = alias["path"]
            if not path:
                row["value"] = copy.deepcopy(value)
            else:
                target = row["value"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = copy.deepcopy(value)
        restored[row["id"]] = row
    return list(restored.values())


def repeated_evidence():
    profile = "A private identity and a precise constraint that must remain available. " * 35
    lore = "An inactive rule still constrains causally possible actions. " * 30
    return [
        {"id": "book.hidden_rule", "kind": "rule", "value": {"content": lore, "visibility": {"scope": "private", "node_ids": ["b"]}}},
        {"id": "entity.a", "kind": "definition", "value": {"profile": profile}},
        {"id": "entity.b", "kind": "definition", "value": {"profile": profile}},
        {"id": "event.17", "kind": "accepted_action", "value": {"action": "A short public utterance.", "event_seq": 17}},
        {"id": "actor.input", "kind": "actual_input", "value": {"role": {"profile": profile}, "context": {
            "memory": [{"content": lore}], "background": {"entries": [{"content": lore}]},
            "received_actions": [{"event_seq": 17}], "visible_dynamic_ids": []}}},
    ]


def test_projection_roundtrips_all_evidence_and_preserves_actor_visibility_without_mutation():
    original = repeated_evidence()
    snapshot = copy.deepcopy(original)
    rows, aliases = project_evidence(original)
    assert aliases >= 4
    assert expand(rows) == snapshot == original
    assert expand(rows)[-1]["value"]["context"]["visible_dynamic_ids"] == []
    assert [row["id"] for row in rows] == [row["id"] for row in original]
    assert "book.hidden_rule" in json.dumps(rows)
    assert all(not row["id"].startswith("event.") or "private" not in json.dumps(row) for row in rows)
    assessment = {"decision": "conflict", "reason": "The inactive rule is relevant.",
                  "scope": {"self_owned": True, "public_only": True}, "findings": [
                      {"category": "causality", "reason": "Established constraint.", "evidence_ids": ["book.hidden_rule", "entity.b"]}]}
    assert parse_assessment(json.dumps(assessment), original)["findings"][0]["evidence_ids"] == ["book.hidden_rule", "entity.b"]


@pytest.mark.parametrize("value", [
    None, False, 0, "", [], {},
    {"refs": [{"source": "fabricated", "path": []}], "content": "literal data " * 100},
    {"a/b~c": ["long text " * 100, None, True, 0]},
    {"nested": {"角色": "A multilingual profile " * 80}},
])
def test_aliases_preserve_nested_shapes_nulls_literals_and_short_values(value):
    evidence = [{"id": str(index), "value": copy.deepcopy(value)} for index in range(3)]
    rows, _ = project_evidence(evidence)
    assert expand(rows) == evidence
    assert project_evidence(evidence) == project_evidence(evidence)


def test_cache_friendly_prefix_and_savings_are_measured_without_storing_evidence_text():
    evidence = repeated_evidence()
    first, metrics = build_check_prompt(evidence, {"actor_id": "a", "action": "First action."}, CHECK_SYSTEM_PROMPT)
    second, _ = build_check_prompt(evidence, {"actor_id": "b", "action": "Different action."}, CHECK_SYSTEM_PROMPT)
    assert first.rsplit(',"CANDIDATE":', 1)[0] == second.rsplit(',"CANDIDATE":', 1)[0]
    assert first.startswith('{"EVIDENCE":')
    assert expand(json.loads(first)["EVIDENCE"]) == evidence
    assert metrics["estimated_input_tokens"] < metrics["unprojected_input_tokens"] * 0.6
    assert sum(block["chars"] for block in metrics["blocks"].values()) == len(first) + len(CHECK_SYSTEM_PROMPT)
    assert "private identity" not in json.dumps(metrics)
    retried = request_metrics(metrics, first + "\nCheck the same candidate again.", CHECK_SYSTEM_PROMPT)
    assert retried["blocks"]["protocol_recovery"]["chars"] > 0
    assert retried["estimated_input_tokens"] > metrics["estimated_input_tokens"]
    assert "protocol_recovery" not in metrics["blocks"]


def test_provider_usage_retains_only_valid_numeric_counters():
    assert response_usage({"raw": {"usage": {
        "prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 70,
        "input_tokens": True, "output_tokens": -1, "secret": "PRIVATE",
        "prompt_tokens_details": {"cached_tokens": 60, "secret": "PRIVATE"},
    }}}) == {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 70, "cached_input_tokens": 60}
    assert response_usage({"raw": {"usage": []}}) == {}


def test_numeric_context_telemetry_survives_redaction_but_credentials_never_do():
    _, metrics = build_check_prompt(repeated_evidence(), {"action": "A public reply."}, CHECK_SYSTEM_PROMPT)
    assert redact_secrets(metrics) == metrics
    for key in ("estimated_input_tokens", "unprojected_input_tokens", "cached_input_tokens", "prompt_cache_hit_tokens"):
        assert redact_secrets({key: "a credential disguised as telemetry"})[key] == "***REDACTED***"
        assert redact_secrets({key: 12})[key] == 12
    assert redact_secrets({"api_key": "PRIVATE", "token": 12}) == {"api_key": "***REDACTED***", "token": "***REDACTED***"}
