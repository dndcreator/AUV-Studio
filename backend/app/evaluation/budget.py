from __future__ import annotations

import asyncio
import copy
import hashlib
import math
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import Any

from ..execution.provider import ProviderError, safe_provider_diagnostics
from ..execution.context_book import estimate_tokens as _estimate_tokens


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
    planning_output_tokens: int = 2400

    def __post_init__(self) -> None:
        if (type(self.max_calls) is not int or self.max_calls < 0 or any(type(value) is not int or value <= 0
                for value in (self.max_total_tokens, self.max_output_tokens_per_call, self.planning_output_tokens))):
            raise ValueError("call allowance must be a nonnegative integer; other budget limits must be positive integers")
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in
               (self.max_cost_usd, self.input_usd_per_million, self.output_usd_per_million)) or self.max_cost_usd <= 0:
            raise ValueError("budget prices and cost ceiling must be finite nonnegative numbers, with a positive ceiling")


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
    def with_visual_mode(self) -> BudgetedProvider:
        scoped = copy.copy(self)
        if hasattr(self.provider, "with_visual_mode"):
            scoped.provider = self.provider.with_visual_mode()
        return scoped

    def with_provider(self, provider: Any) -> BudgetedProvider:
        """An independent judge context shares the same physical-attempt budget, never a fresh allowance."""
        scoped = copy.copy(self)
        scoped.provider = provider
        if hasattr(provider, "retry_count"):
            provider.retry_count = 0
        if hasattr(provider, "max_output_tokens"):
            provider.max_output_tokens = self.budget.max_output_tokens_per_call
        return scoped

    @property
    def planning_output_tokens(self) -> int:
        return self.budget.planning_output_tokens

    def with_output_limit(self, limit: int) -> BudgetedProvider:
        scoped = copy.copy(self)
        scoped.budget = replace(self.budget, max_output_tokens_per_call=limit)
        if hasattr(self.provider, "with_output_limit"):
            scoped.provider = self.provider.with_output_limit(limit)
        # Usage, retries and categories deliberately remain shared with the parent budget.
        return scoped

    def remaining_budget(self) -> dict[str, Any]:
        return {"remaining_calls": max(0, self.budget.max_calls - self.usage.calls),
                "remaining_tokens": max(0, self.budget.max_total_tokens - self.usage.input_tokens - self.usage.output_tokens),
                "remaining_estimated_cost_usd": max(0, round(self.budget.max_cost_usd - self.usage.estimated_cost_usd(self.budget), 6))}

    def __init__(self, provider: Any, budget: EvalBudget, *, retry_count: int = 0, capture_trace: bool = False) -> None:
        self.provider = provider
        self.budget = budget
        self.usage = EvalUsage()
        self.retry_count = max(0, min(retry_count, 2))
        self.retries: list[dict[str, Any]] = []
        self.usage_by_kind: dict[str, dict[str, int]] = {}
        self.capture_trace = capture_trace
        self.request_trace: list[dict[str, Any]] = []
        self.started_at = perf_counter()
        # Shared by scoped copies; reservation occurs without an await on this event loop.
        self.inflight = {"input_tokens": 0, "output_tokens": 0}
        # Count each physical attempt here instead of hiding transport retries inside it.
        if hasattr(provider, "retry_count"):
            provider.retry_count = 0
        if hasattr(provider, "max_output_tokens"):
            provider.max_output_tokens = budget.max_output_tokens_per_call

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        for attempt in range(self.retry_count + 1):
            try:
                return await self._chat_once(model, system_prompt, user_prompt)
            except ProviderError as exc:
                if not exc.retryable or attempt >= self.retry_count:
                    raise
                self.retries.append({"after_call": self.usage.calls, "code": exc.code, "status_code": exc.status_code})
                await asyncio.sleep(2**attempt)
        raise AssertionError("unreachable retry state")

    async def _chat_once(self, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        estimated_input = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)
        self._check_before_call(estimated_input)
        reserved_output = self.budget.max_output_tokens_per_call
        self.inflight["input_tokens"] += estimated_input
        self.inflight["output_tokens"] += reserved_output
        self.usage.calls += 1
        started = perf_counter()
        trace: dict[str, Any] = {"attempt": self.usage.calls, "kind": request_kind(system_prompt),
                                 "start_offset_ms": round((started - self.started_at) * 1000),
                                 "input_digest": hashlib.sha256((system_prompt + "\0" + user_prompt).encode()).hexdigest()}
        if self.capture_trace:
            self.request_trace.append(trace)
        try:
            response = await self.provider.chat(model=model, system_prompt=system_prompt, user_prompt=user_prompt)
        except (Exception, asyncio.CancelledError) as exc:
            # An HTTP-200 empty response can still report usage; reserve only missing counters.
            diagnostics = safe_provider_diagnostics(getattr(exc, "diagnostics", None))
            supplied_input = diagnostics.get("prompt_tokens")
            supplied_output = diagnostics.get("completion_tokens")
            input_tokens = estimated_input if supplied_input is None else supplied_input
            output_tokens = reserved_output if supplied_output is None else supplied_output
            self._record_usage(system_prompt, input_tokens, output_tokens, failed=True)
            trace.update(status="error", error_type=type(exc).__name__, error_code=getattr(exc, "code", None),
                         input_tokens=input_tokens, output_tokens=output_tokens,
                         token_source={"input": "provider" if supplied_input is not None else "estimate",
                                       "output": "provider" if supplied_output is not None else "reserved"},
                         duration_ms=round((perf_counter() - started) * 1000))
            if diagnostics:
                trace["provider_diagnostics"] = diagnostics
                if "finish_reason" in diagnostics:
                    trace["finish_reason"] = diagnostics["finish_reason"]
            raise
        finally:
            self.inflight["input_tokens"] -= estimated_input
            self.inflight["output_tokens"] -= reserved_output
        raw = response.get("raw", {}) if isinstance(response, dict) else {}
        raw_usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        supplied_input = _usage_int(raw_usage, "prompt_tokens", "input_tokens")
        supplied_output = _usage_int(raw_usage, "completion_tokens", "output_tokens")
        input_tokens = estimated_input if supplied_input is None else supplied_input
        output_tokens = _estimate_tokens(str(response.get("content", ""))) if supplied_output is None else supplied_output
        self._record_usage(system_prompt, input_tokens, output_tokens)
        choices = raw.get("choices", []) if isinstance(raw, dict) else []
        finish = choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        trace.update(status="received", input_tokens=input_tokens, output_tokens=output_tokens,
                     token_source={"input": "provider" if supplied_input is not None else "estimate",
                                   "output": "provider" if supplied_output is not None else "estimate"},
                     output_digest=hashlib.sha256(str(response.get("content", "")).encode()).hexdigest(),
                     finish_reason=str(finish or raw.get("done_reason", "unknown"))[:80] if isinstance(raw, dict) else "unknown",
                     duration_ms=round((perf_counter() - started) * 1000))
        if self.usage.input_tokens + self.usage.output_tokens > self.budget.max_total_tokens:
            raise EvalBudgetExceeded("evaluation token budget exceeded")
        if self.usage.estimated_cost_usd(self.budget) > self.budget.max_cost_usd:
            raise EvalBudgetExceeded("evaluation cost budget exceeded")
        return response

    def _record_usage(self, system: str, input_tokens: int, output_tokens: int, *, failed: bool = False) -> None:
        kind = request_kind(system)
        row = self.usage_by_kind.setdefault(kind, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0})
        for key, value in {"calls": 1, "input_tokens": input_tokens, "output_tokens": output_tokens, "failed": int(failed)}.items():
            row[key] += value
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens

    def _check_before_call(self, estimated_input: int) -> None:
        if self.usage.calls >= self.budget.max_calls:
            raise EvalBudgetExceeded("evaluation call budget exceeded")
        projected_tokens = (self.usage.input_tokens + self.usage.output_tokens + sum(self.inflight.values())
                            + estimated_input + self.budget.max_output_tokens_per_call)
        if projected_tokens > self.budget.max_total_tokens:
            raise EvalBudgetExceeded("next call would exceed the evaluation token budget")
        projected_cost = (
            (self.usage.input_tokens + self.inflight["input_tokens"] + estimated_input) * self.budget.input_usd_per_million
            + (self.usage.output_tokens + self.inflight["output_tokens"] + self.budget.max_output_tokens_per_call) * self.budget.output_usd_per_million
        ) / 1_000_000
        if projected_cost > self.budget.max_cost_usd:
            raise EvalBudgetExceeded("next call would exceed the evaluation cost budget")


def request_kind(system: str) -> str:
    categories = {"visual director": "visual", "independent evidence-based regression judge": "judge", "fixed criteria": "judge",
                  "action consistency checker": "action_check", "literary fidelity checker": "writing_check",
                  "process architect": "planning", "route attention": "routing", "continuity auditor": "audit",
                  "state coder repairing": "state_repair", "state coder": "state_coder",
                  "player-controlled actor": "player_actor", "autonomous participant": "actor",
                  "finished literary work": "writing"}
    return next((kind for marker, kind in categories.items() if marker in system.casefold()), "other")


def _usage_int(usage: Any, *keys: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for key in keys:
        value = usage.get(key)
        if type(value) is int and value >= 0:
            return value
    return None
