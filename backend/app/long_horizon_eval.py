from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .db import SessionLocal
from .eval_runner import _safe_base_url
from .evaluation.budget import BudgetedProvider, EvalBudget
from .evaluation.long_horizon import build_long_horizon_checkpoints, evaluate_long_horizon, probe_prompt
from .execution.provider import build_provider, provider_accepts_keyless
from .model_config_service import apply_stored_model_config


SCENARIO_PATH = Path(__file__).resolve().parents[1] / "evals" / "long_horizon" / "worldbook_distributed.json"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results" / "long_horizon"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the opt-in AUV long-horizon dependency evaluation.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--simulate", action="store_true", help="Run the zero-cost deterministic simulation")
    mode.add_argument("--execute", action="store_true", help="Call the configured model at sparse checkpoints")
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--max-total-tokens", type=int, default=16_000)
    parser.add_argument("--max-output-tokens", type=int, default=500)
    parser.add_argument("--max-cost-usd", type=float, default=0.08)
    parser.add_argument("--input-usd-per-million", type=float, default=1.00)
    parser.add_argument("--output-usd-per-million", type=float, default=4.00)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scenario = _load_scenario()
        checkpoints = build_long_horizon_checkpoints(scenario)
        budget = _build_budget(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(f"Scenario: {scenario['title']}")
    print(f"Timeline: {scenario['turns']} turns; sparse checkpoints: {len(checkpoints)}")
    print(f"Real-call hard limit: {budget.max_calls}; token hard limit: {budget.max_total_tokens}")
    print(f"Estimated-cost hard limit: ${budget.max_cost_usd:.2f}")
    if not args.simulate and not args.execute:
        print("Preflight only. Add --simulate for zero-cost checks or --execute for real-model probes.")
        return 0
    if len(checkpoints) > budget.max_calls and args.execute:
        print("[ERROR] checkpoint count exceeds the real-call hard limit", file=sys.stderr)
        return 2

    try:
        artifact = asyncio.run(_run_real(scenario, checkpoints, budget)) if args.execute else _run_simulated(scenario, checkpoints, budget)
        json_path, markdown_path = _write_artifact(artifact)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] long-horizon evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Mode: {artifact['evaluation']['mode']}; score: {artifact['evaluation']['score']}/100")
    print(f"JSON: {json_path}")
    print(f"Review: {markdown_path}")
    return 0 if not artifact["evaluation"]["failures"] else 1


def _load_scenario() -> dict[str, Any]:
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("probes"), list):
        raise ValueError("invalid long-horizon scenario")
    node_ids = {str(node.get("id")) for node in raw["nodes"] if isinstance(node, dict)}
    if not node_ids or any(str(probe.get("node_id")) not in node_ids for probe in raw["probes"]):
        raise ValueError("long-horizon probe references an unknown node")
    if not 1 <= int(raw.get("turns", 0)) <= 500:
        raise ValueError("long-horizon turns must be between 1 and 500")
    if not 1 <= len(raw["probes"]) <= 8:
        raise ValueError("long-horizon scenario must contain between 1 and 8 probes")
    return raw


def _build_budget(args: argparse.Namespace) -> EvalBudget:
    if not 1 <= args.max_calls <= 8:
        raise ValueError("max-calls must be between 1 and 8")
    if not 1_000 <= args.max_total_tokens <= 40_000:
        raise ValueError("max-total-tokens must be between 1000 and 40000")
    if not 64 <= args.max_output_tokens <= 1_000:
        raise ValueError("max-output-tokens must be between 64 and 1000")
    if not 0.01 <= args.max_cost_usd <= 0.25:
        raise ValueError("max-cost-usd must be between 0.01 and 0.25")
    if args.input_usd_per_million < 0 or args.output_usd_per_million < 0:
        raise ValueError("price assumptions cannot be negative")
    return EvalBudget(
        max_calls=args.max_calls,
        max_total_tokens=args.max_total_tokens,
        max_output_tokens_per_call=args.max_output_tokens,
        max_cost_usd=args.max_cost_usd,
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
    )


def _run_simulated(scenario: dict[str, Any], checkpoints: list[dict[str, Any]], budget: EvalBudget) -> dict[str, Any]:
    return _artifact(scenario, budget, evaluate_long_horizon(scenario, checkpoints), usage=None, model=None)


async def _run_real(
    scenario: dict[str, Any], checkpoints: list[dict[str, Any]], budget: EvalBudget
) -> dict[str, Any]:
    provider = build_provider()
    db = SessionLocal()
    try:
        apply_stored_model_config(db, provider)
    finally:
        db.close()
    if not provider.api_key and not provider_accepts_keyless(provider.provider_kind, provider.base_url):
        raise RuntimeError("configured provider has no API key; open Model Setup or set AUV_OPENAI_API_KEY")
    provider.retry_count = 0
    if (urlsplit(provider.base_url).hostname or "").lower().endswith("deepseek.com"):
        provider.thinking_mode = "disabled"
    limited = BudgetedProvider(provider, budget)
    responses: dict[str, str] = {}
    for checkpoint in checkpoints:
        response = await limited.chat(
            model="",
            system_prompt="You evaluate one sparse checkpoint in a distributed long-horizon simulation.",
            user_prompt=probe_prompt(checkpoint),
        )
        responses[checkpoint["id"]] = str(response.get("content", ""))
    evaluation = evaluate_long_horizon(scenario, checkpoints, responses)
    model = {
        "provider": provider.provider_kind,
        "base_url": _safe_base_url(provider.base_url),
        "model": provider.default_model,
        "thinking_mode": getattr(provider, "thinking_mode", None),
    }
    return _artifact(scenario, budget, evaluation, usage=limited.usage.model_dump(budget), model=model)


def _artifact(
    scenario: dict[str, Any],
    budget: EvalBudget,
    evaluation: dict[str, Any],
    *,
    usage: dict[str, Any] | None,
    model: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario": {"title": scenario["title"], "version": scenario.get("version", 1)},
        "budget": asdict(budget),
        "usage": usage or {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0},
        "model": model,
        "evaluation": evaluation,
    }


def _write_artifact(artifact: dict[str, Any]) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = RESULTS_DIR / f"worldbook-distributed-{stamp}.json"
    markdown_path = RESULTS_DIR / f"worldbook-distributed-{stamp}.md"
    json_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    markdown_path.write_text(_markdown(artifact), encoding="utf-8")
    return json_path, markdown_path


def _markdown(artifact: dict[str, Any]) -> str:
    evaluation = artifact["evaluation"]
    lines = [
        "# Long-Horizon Evaluation",
        "",
        f"- Mode: `{evaluation['mode']}`",
        f"- Score: `{evaluation['score']}/100`",
        f"- Turns: `{evaluation['stats']['turns']}`",
        f"- Checkpoints: `{evaluation['stats']['checkpoint_count']}`",
        f"- Calls: `{artifact['usage']['calls']}`",
        f"- Estimated cost: `${artifact['usage']['estimated_cost_usd']}`",
        "",
        "## Failures",
        "",
    ]
    lines.extend([f"- {item}" for item in evaluation["failures"]] or ["- None"])
    lines.extend(["", "## Checkpoints", ""])
    for item in evaluation["checkpoints"]:
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- Turn: `{item['turn']}`",
                f"- Node: `{item['node_id']}`",
                f"- Context estimate: `{item['estimated_tokens']}` tokens",
                *[f"- [{'x' if passed else ' '}] {name}" for name, passed in item["checks"].items()],
                "",
            ]
        )
        if item["response"]:
            lines.extend(["Response:", "", "```text", item["response"], "```", ""])
    lines.extend(["## Manual Review", "", "- [ ] Early facts affect late decisions.", "- [ ] Roles remain distinct.", "- [ ] Private knowledge does not leak.", "- [ ] World Book rules influence behavior rather than appearing as empty recall.", "- [ ] Late actions remain natural under accumulated context.", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
