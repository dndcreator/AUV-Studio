from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db import Base, SessionLocal
from .evaluation.budget import BudgetedProvider, EvalBudget
from .evaluation.quality import evaluate_run, judge_run
from .evaluation.scenario import list_scenarios, load_scenario
from .execution.engine import WorkflowEngine
from .execution.provider import build_provider, provider_accepts_keyless
from .model_config_service import apply_stored_model_config
from .models import WorkflowRecord
from .schemas import RunRequest
from .service import get_events, get_full_log, get_run, get_run_metrics


RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an opt-in real-model AUV quality evaluation.")
    parser.add_argument("--scenario", default="roleplay_basic", help="Fixed scenario name")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--execute", action="store_true", help="Actually call the configured model")
    parser.add_argument("--judge", action="store_true", help="Spend one additional call on an advisory LLM review")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-total-tokens", type=int, default=26_000)
    parser.add_argument("--max-output-tokens", type=int, default=600)
    parser.add_argument("--max-cost-usd", type=float, default=0.10)
    parser.add_argument("--input-usd-per-million", type=float, default=1.00)
    parser.add_argument("--output-usd-per-million", type=float, default=4.00)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        print("\n".join(list_scenarios()))
        return 0
    try:
        scenario, workflow = load_scenario(args.scenario)
        budget = _build_budget(args)
        _validate_scenario_safety(workflow)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    estimated_calls = int(scenario.get("estimated_calls", 0)) + (1 if args.judge else 0)
    print(f"Scenario: {scenario.get('title', args.scenario)}")
    print(f"Expected calls: about {estimated_calls}; hard limit: {budget.max_calls}")
    print(f"Token hard limit: {budget.max_total_tokens}; output/call: {budget.max_output_tokens_per_call}")
    print(f"Cost hard limit: ${budget.max_cost_usd:.2f} (using the supplied price assumptions)")
    print("Billing note: the USD limit is an estimate; call and token limits are the enforceable safeguards.")
    if estimated_calls > budget.max_calls:
        print("[ERROR] expected calls exceed the configured hard limit", file=sys.stderr)
        return 2
    if not args.execute:
        print("Preflight only. Add --execute to make real model calls.")
        return 0

    try:
        artifact, json_path, markdown_path = asyncio.run(_execute(args.scenario, scenario, workflow, budget, args.judge))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] evaluation could not start: {exc}", file=sys.stderr)
        return 1
    print(f"Run status: {artifact['run']['status']}")
    print(f"Rule score: {artifact['deterministic_evaluation']['score']}/100")
    print(f"Calls: {artifact['usage']['calls']}; estimated tokens: {artifact['usage']['input_tokens'] + artifact['usage']['output_tokens']}")
    print(f"Estimated cost: ${artifact['usage']['estimated_cost_usd']:.6f}")
    print(f"JSON: {json_path}")
    print(f"Review: {markdown_path}")
    return 0 if artifact["run"]["status"] == "succeeded" else 1


def _build_budget(args: argparse.Namespace) -> EvalBudget:
    if not 1 <= args.max_calls <= 40:
        raise ValueError("max-calls must be between 1 and 40")
    if not 1_000 <= args.max_total_tokens <= 100_000:
        raise ValueError("max-total-tokens must be between 1000 and 100000")
    if not 64 <= args.max_output_tokens <= 4_000:
        raise ValueError("max-output-tokens must be between 64 and 4000")
    if not 0.01 <= args.max_cost_usd <= 5.0:
        raise ValueError("max-cost-usd must be between 0.01 and 5.00")
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


async def _execute(
    scenario_name: str,
    scenario: dict[str, Any],
    workflow: Any,
    budget: EvalBudget,
    use_judge: bool,
) -> tuple[dict[str, Any], Path, Path]:
    provider = build_provider()
    config_db = SessionLocal()
    try:
        apply_stored_model_config(config_db, provider)
    finally:
        config_db.close()
    if not provider.api_key and not provider_accepts_keyless(provider.provider_kind, provider.base_url):
        raise RuntimeError("configured provider has no API key; open Model Setup or set AUV_OPENAI_API_KEY")
    # One logical eval call must equal one provider request for predictable spend.
    provider.retry_count = 0
    if (urlsplit(provider.base_url).hostname or "").lower().endswith("deepseek.com"):
        provider.thinking_mode = "disabled"

    limited_provider = BudgetedProvider(provider, budget)
    engine = WorkflowEngine(provider=limited_provider)  # type: ignore[arg-type]
    eval_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eval_engine)
    db = sessionmaker(bind=eval_engine, autocommit=False, autoflush=False)()
    try:
        db.add(
            WorkflowRecord(
                id=workflow.id,
                name=workflow.name,
                version=workflow.version,
                definition_json=workflow.model_dump_json(),
            )
        )
        db.commit()
        run_id = await engine.run(db=db, workflow=workflow, request=RunRequest(input=scenario.get("input", {})))
        detail = get_run(db, run_id)
        events = get_events(db, run_id, after_seq=0, limit=10_000)
        metrics = get_run_metrics(db, run_id)
        full_log = get_full_log(db, run_id, "markdown")
        deterministic = evaluate_run(
            detail.model_dump(mode="json"),
            scenario.get("expected", {}),
            [event.model_dump(mode="json") for event in events.events],
        )
        llm_judge: dict[str, Any] | None = None
        if use_judge and detail.status == "succeeded":
            try:
                llm_judge = await judge_run(limited_provider, scenario.get("rubric", {}), full_log.content)
            except Exception as exc:  # noqa: BLE001
                llm_judge = {"error": str(exc)}
        previous = _latest_result(scenario_name)
        comparison = _compare_previous(previous, deterministic, metrics.estimated_token_usage)
        artifact = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scenario": {"id": scenario_name, "title": scenario.get("title", scenario_name), "version": scenario.get("version", 1)},
            "model": {
                "provider": provider.provider_kind,
                "base_url": _safe_base_url(provider.base_url),
                "model": provider.default_model,
                "thinking_mode": getattr(provider, "thinking_mode", None),
            },
            "budget": asdict(budget),
            "usage": limited_provider.usage.model_dump(budget),
            "run": {"id": run_id, "status": detail.status, "error": detail.error},
            "metrics": metrics.model_dump(mode="json"),
            "deterministic_evaluation": deterministic,
            "llm_judge": llm_judge,
            "comparison": comparison,
            "full_log": full_log.content,
        }
    finally:
        db.close()
        eval_engine.dispose()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = RESULTS_DIR / f"{scenario_name}-{stamp}.json"
    markdown_path = RESULTS_DIR / f"{scenario_name}-{stamp}.md"
    json_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    markdown_path.write_text(_review_markdown(artifact), encoding="utf-8")
    return artifact, json_path, markdown_path


def _latest_result(scenario_name: str) -> dict[str, Any] | None:
    candidates = sorted(RESULTS_DIR.glob(f"{scenario_name}-*.json"), reverse=True)
    if not candidates:
        return None
    try:
        value = json.loads(candidates[0].read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _compare_previous(previous: dict[str, Any] | None, current: dict[str, Any], current_tokens: int) -> dict[str, Any]:
    if not previous:
        return {"verdict": "first_run", "score_delta": None, "token_delta": None}
    old_score = float(previous.get("deterministic_evaluation", {}).get("score", 0))
    old_tokens = int(previous.get("metrics", {}).get("estimated_token_usage", 0))
    delta = round(float(current.get("score", 0)) - old_score, 1)
    verdict = "improved" if delta >= 5 else "regressed" if delta <= -5 else "stable"
    return {"verdict": verdict, "score_delta": delta, "token_delta": current_tokens - old_tokens}


def _validate_scenario_safety(workflow: Any) -> None:
    if len(workflow.nodes) > 8:
        raise ValueError("evaluation scenarios may contain at most 8 nodes")
    forbidden = {"tool", "external_agent", "human_checkpoint"}
    used_forbidden = sorted({node.type for node in workflow.nodes if node.type in forbidden})
    if used_forbidden:
        raise ValueError(f"evaluation scenarios cannot use side-effecting nodes: {', '.join(used_forbidden)}")
    simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
    if int(simulation.get("horizon_rounds", 1) or 1) > 6:
        raise ValueError("evaluation horizon_rounds cannot exceed 6")
    if int(simulation.get("max_actions", 0) or 0) > 12:
        raise ValueError("evaluation max_actions cannot exceed 12")


def _safe_base_url(value: str) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _review_markdown(artifact: dict[str, Any]) -> str:
    evaluation = artifact["deterministic_evaluation"]
    usage = artifact["usage"]
    comparison = artifact["comparison"]
    judge = artifact.get("llm_judge")
    findings = evaluation.get("findings", []) or ["No deterministic failures detected."]
    actions = evaluation.get("actions", [])
    lines = [
        f"# AUV Eval Review: {artifact['scenario']['title']}",
        "",
        f"- Run: `{artifact['run']['id']}` / `{artifact['run']['status']}`",
        f"- Model: `{artifact['model']['provider']}` / `{artifact['model']['model']}`",
        f"- Rule score: `{evaluation['score']}/100`",
        f"- Comparison: `{comparison['verdict']}`",
        f"- Calls: `{usage['calls']}`",
        f"- Estimated tokens: `{usage['input_tokens'] + usage['output_tokens']}`",
        f"- Estimated cost: `${usage['estimated_cost_usd']:.6f}`",
        "",
        "## Findings",
        "",
        *[f"- {item}" for item in findings],
        "",
        "## Actor Actions",
        "",
        *[f"- **{item['node_id']}**: {item['action']}" for item in actions],
    ]
    if judge is not None:
        lines.extend(["", "## Advisory LLM Judge", "", "```json", json.dumps(judge, ensure_ascii=False, indent=2), "```"])
    lines.extend(
        [
            "",
            "## Manual Review",
            "",
            "- [ ] Roles remain consistent and do not share private knowledge incorrectly.",
            "- [ ] Actors make autonomous decisions instead of waiting for Director-authored actions.",
            "- [ ] Each episode changes the situation rather than restating it.",
            "- [ ] Director audits continuity and boundaries without taking over the simulation.",
            "- [ ] Final output is faithful to the observed process.",
            "- [ ] Quality justifies the measured latency and cost.",
            "",
            "## Full Log",
            "",
            artifact["full_log"],
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
