from __future__ import annotations

import asyncio
import json

import pytest

from app.evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from app.execution.provider import ProviderError
from app.narrative_fidelity import NarrativeFidelityError, generate_grounded_narrative, parse_review


SOURCE = 'Mina asks: "How large are the rats? I have not decided yet."'
BAD = '"Cat-sized," the clerk replies. Mina signs the contract.'
GOOD = 'Mina held the paper. "How big are they?" Her voice was quieter now.'
ACCEPT = '{"decision":"accept","findings":[]}'
REVISE = json.dumps({"decision": "revise", "findings": [{
    "candidate_quote": '"Cat-sized," the clerk replies.', "source_quote": 'How large are the rats?',
    "reason": "The source stops at an unanswered question; this adds a reply and risk fact.",
}]})


class Sequence:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.prompts = []

    async def chat(self, **kwargs):
        self.prompts.append(kwargs)
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output if isinstance(output, dict) else {"content": output}


def generate(provider):
    return asyncio.run(generate_grounded_narrative(
        provider, source=SOURCE, style="restrained screenplay", language="English", audience="general", length="short",
    ))


def test_supported_expressive_detail_is_published_only_after_review():
    provider = Sequence(GOOD, ACCEPT)
    text, audit = generate(provider)
    assert text == GOOD
    assert audit["checks"][0]["decision"] == "accept"
    assert len(audit["source_hash"]) == len(audit["checks"][0]["candidate_hash"]) == 64
    assert "restrained screenplay" in provider.prompts[0]["user_prompt"]
    assert json.loads(provider.prompts[1]["user_prompt"]) == {"source": SOURCE, "candidate": GOOD}


def test_invented_reply_is_repaired_once_without_changing_the_source():
    provider = Sequence(BAD, REVISE, GOOD, ACCEPT)
    text, audit = generate(provider)
    assert text == GOOD
    assert [row["decision"] for row in audit["checks"]] == ["revise", "accept"]
    assert SOURCE in provider.prompts[2]["user_prompt"]
    assert "rejected_draft" in provider.prompts[2]["user_prompt"]
    assert BAD not in json.loads(provider.prompts[3]["user_prompt"])["source"]


def test_persistent_invention_is_not_returned_as_a_valid_work():
    provider = Sequence(BAD, REVISE, BAD, REVISE)
    with pytest.raises(NarrativeFidelityError) as error:
        generate(provider)
    assert error.value.code == "narrative_fidelity_failed"
    assert len(provider.prompts) == 4
    assert len(error.value.checks) == 2
    assert BAD not in str(error.value)


@pytest.mark.parametrize("review", [
    "not json", '{"decision":"accept","findings":[{}]}',
    '{"decision":"revise","findings":[]}',
    REVISE.replace("How large are the rats?", "nonexistent source"),
    REVISE.replace("Cat-sized", "Dog-sized"),
    REVISE.replace('"reason":', '"extra": 42, "reason":'),
])
def test_malformed_or_fabricated_review_fails_closed(review):
    provider = Sequence(BAD, review)
    with pytest.raises(NarrativeFidelityError) as error:
        generate(provider)
    assert error.value.code == "narrative_invalid_review"
    assert len(provider.prompts) == 2


def test_fenced_review_is_supported():
    assert parse_review("```json\n" + ACCEPT + "\n```", SOURCE, GOOD).decision == "accept"


@pytest.mark.parametrize("response,code", [
    ({"content": ""}, "narrative_empty_output"),
    ({"content": GOOD, "raw": {"mock": True}}, "model_not_configured"),
    ({"content": GOOD, "raw": {"choices": [{"finish_reason": "length"}]}}, "narrative_output_truncated"),
    ({"content": GOOD, "raw": {"done_reason": "length"}}, "narrative_output_truncated"),
])
def test_empty_mock_or_truncated_work_is_never_published(response, code):
    provider = Sequence(response)
    with pytest.raises(NarrativeFidelityError) as error:
        generate(provider)
    assert error.value.code == code
    assert len(provider.prompts) == 1


def test_review_transport_failure_propagates_without_silent_publication():
    failure = ProviderError("network error", code="network_error")
    with pytest.raises(ProviderError) as error:
        generate(Sequence(GOOD, failure))
    assert error.value is failure


def test_insufficient_review_budget_stops_before_the_next_call():
    inner = Sequence(GOOD, ACCEPT)
    provider = BudgetedProvider(inner, EvalBudget(max_calls=1, max_total_tokens=10000, max_cost_usd=1))
    with pytest.raises(EvalBudgetExceeded, match="call budget"):
        generate(provider)
    assert len(inner.prompts) == provider.usage.calls == 1


def test_all_four_correction_calls_are_metered():
    inner = Sequence(BAD, REVISE, GOOD, ACCEPT)
    provider = BudgetedProvider(inner, EvalBudget(max_calls=4, max_total_tokens=10000, max_cost_usd=1))
    assert generate(provider)[0] == GOOD
    assert provider.usage.calls == 4
