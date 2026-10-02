from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class EvalBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class EvalBudget:
    max_calls: int = 12
    max_total_tokens: int = 20_000
    max_output_tokens_per_call: int = 500
    max_cost_usd: float = 0.50
    input_usd_per_million: float = 1.00
    output_usd_per_million: float = 4.00


@dataclass
class EvalUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def estimated_cost_usd(self, budget: EvalBudget) -> float:
        return (
            self.input_tokens * budget.input_usd_per_million
            + self.output_tokens * budget.output_usd_per_million
        ) / 1_000_000

    def model_dump(self, budget: EvalBudget) -> dict[str, Any]:
        return {**asdict(self), "estimated_cost_usd": round(self.estimated_cost_usd(budget), 6)}


class BudgetedProvider:
    def __init__(self, provider: Any, budget: EvalBudget) -> None:
        self.provider = provider
        self.budget = budget
        self.usage = EvalUsage()
        if hasattr(provider, "max_output_tokens"):
            provider.max_output_tokens = budget.max_output_tokens_per_call

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        estimated_input = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)
        self._check_before_call(estimated_input)
        self.usage.calls += 1
        try:
            response = await self.provider.chat(model=model, system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception:
            # Failed requests may still be billed. Record the conservative reserved amount.
            self.usage.input_tokens += estimated_input
            self.usage.output_tokens += self.budget.max_output_tokens_per_call
            raise
        raw = response.get("raw", {}) if isinstance(response, dict) else {}
        raw_usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        input_tokens = _usage_int(raw_usage, "prompt_tokens", "input_tokens") or estimated_input
        output_tokens = _usage_int(raw_usage, "completion_tokens", "output_tokens") or _estimate_tokens(str(response.get("content", "")))
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        if self.usage.input_tokens + self.usage.output_tokens > self.budget.max_total_tokens:
            raise EvalBudgetExceeded("evaluation token budget exceeded")
        if self.usage.estimated_cost_usd(self.budget) > self.budget.max_cost_usd:
            raise EvalBudgetExceeded("evaluation cost budget exceeded")
        return response

    def _check_before_call(self, estimated_input: int) -> None:
        if self.usage.calls >= self.budget.max_calls:
            raise EvalBudgetExceeded("evaluation call budget exceeded")
        projected_tokens = self.usage.input_tokens + self.usage.output_tokens + estimated_input + self.budget.max_output_tokens_per_call
        if projected_tokens > self.budget.max_total_tokens:
            raise EvalBudgetExceeded("next call would exceed the evaluation token budget")
        projected_cost = (
            (self.usage.input_tokens + estimated_input) * self.budget.input_usd_per_million
            + (self.usage.output_tokens + self.budget.max_output_tokens_per_call) * self.budget.output_usd_per_million
        ) / 1_000_000
        if projected_cost > self.budget.max_cost_usd:
            raise EvalBudgetExceeded("next call would exceed the evaluation cost budget")


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _usage_int(usage: Any, *keys: str) -> int:
    if not isinstance(usage, dict):
        return 0
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return 0
