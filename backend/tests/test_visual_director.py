import asyncio
import json

import pytest

from app.schemas import WorkflowDefinition
from app.visual_director import generate_visual, parse_visual


def workflow():
    return WorkflowDefinition.model_validate({"id": "visual", "name": "visual", "nodes": [{"id": "a", "type": "agent", "position": {"x": 0, "y": 0},
        "config": {"entity_name": "A", "entity_profile": "PRIVATE-SECRET", "model_api_key": "PRIVATE-KEY"}}], "edges": [], "environment": {"scenario": "A ship with failing power"}})


def test_visual_suite_is_versioned_and_runs_actual_product_target():
    from app.evaluation.benchmarks import load_cases
    from app.evaluation.targets import run_target
    _, cases = load_cases(suite="visual")
    assert {case["id"] for case in cases} == {"visual_dungeon", "visual_ship"}
    assert all(case["gold"]["review_status"] == "pending_human" for case in cases)
    class Provider:
        async def chat(self, **_request):
            return {"content": json.dumps(plan()), "raw": {}}
    result = asyncio.run(run_target(cases[0], Provider()))
    assert result["presentation"]["renderer"] == "spatial"


def test_visual_stage_scope_keeps_parent_reasoning_and_shared_budget(monkeypatch):
    from app.execution.provider import OpenAICompatibleProvider
    from app.evaluation.budget import BudgetedProvider, EvalBudget
    payloads = []
    async def chat(self, **_request):
        payloads.append((self.thinking_mode, self.max_output_tokens))
        return {"content": json.dumps(plan()), "raw": {"usage": {"prompt_tokens": 50, "completion_tokens": 100}}}
    monkeypatch.setattr(OpenAICompatibleProvider, "chat", chat)
    raw = OpenAICompatibleProvider("https://api.deepseek.com", "test-key", 30, 0, "deepseek-v4-flash")
    provider = BudgetedProvider(raw, EvalBudget(max_calls=2, max_total_tokens=10000))
    asyncio.run(generate_visual(provider, workflow(), {}))
    assert payloads == [("disabled", 2400)]
    assert raw.thinking_mode is None and provider.usage.calls == 1
    assert provider.usage.input_tokens == 50 and provider.usage.output_tokens == 100
    proxy = OpenAICompatibleProvider("https://proxy.invalid", "test", 30, 0, "deepseek-v4-flash")
    assert proxy.with_visual_mode().thinking_mode is None


def plan(color="#FF8800", shape="circle"):
    return {"version": 1, "renderer": "spatial", "theme": {"background": "#111111", "surface": "#333333", "ink": "#FFFFFF", "accent": color},
        "regions": [{"id": "room", "label": "Room", "x": 10, "y": 10, "width": 500, "height": 350}],
        "placements": [], "primitives": [{"id": "light", "shape": shape, "x": 10, "y": 10, "color": color}], "bindings": []}


@pytest.mark.parametrize("color,shape", [("#FF8800", "circle"), ("#44AAFF", "line")])
def test_visual_model_uses_bounded_independent_request_without_private_role_data(color, shape):
    class Provider:
        def with_output_limit(self, value):
            assert value == 2400
            return self

        async def chat(self, **request):
            assert "PRIVATE" not in request["user_prompt"]
            assert "failing power" in request["user_prompt"]
            return {"content": json.dumps(plan(color, shape)), "raw": {}}

    result = asyncio.run(generate_visual(Provider(), workflow(), {}))
    assert result.primitives[0].shape == shape and result.primitives[0].color == color


@pytest.mark.parametrize("change", ["private", "unknown_node", "script", "duplicate", "unknown_region"])
def test_invalid_visual_plan_does_not_become_a_scene(change):
    value = plan()
    if change == "private":
        value["bindings"] = [{"kind": "color", "concept_id": "secret", "target_id": "light", "values": {"on": "#FF0000"}}]
    elif change == "unknown_node":
        value["placements"] = [{"node_id": "ghost", "region_id": "room", "x": 0, "y": 0}]
    elif change == "script": value["primitives"][0]["color"] = "url(javascript:evil)"
    elif change == "duplicate": value["regions"] *= 2
    else: value["placements"] = [{"node_id": "a", "region_id": "unknown", "x": 0, "y": 0}]
    with pytest.raises(ValueError): parse_visual(json.dumps(value), workflow(), {"secret": {"visibility": "private"}})


def test_truncation_or_mock_fails_closed_without_regeneration():
    class Provider:
        calls = 0
        async def chat(self, **_request):
            self.calls += 1
            return {"content": json.dumps(plan()), "raw": {"choices": [{"finish_reason": "length"}]}}
    provider = Provider()
    with pytest.raises(ValueError, match="truncated"):
        asyncio.run(generate_visual(provider, workflow(), {}))
    assert provider.calls == 1


def test_vertical_and_horizontal_svg_lines_are_valid_but_degenerate_shapes_are_not():
    value = plan(shape="line")
    value["primitives"][0].update(width=0, height=100)
    assert parse_visual(json.dumps(value), workflow(), {}).primitives[0].width == 0
    value["primitives"][0].update(width=100, height=0)
    assert parse_visual(json.dumps(value), workflow(), {}).primitives[0].height == 0
    value["primitives"][0].update(width=0)
    with pytest.raises(ValueError): parse_visual(json.dumps(value), workflow(), {})
