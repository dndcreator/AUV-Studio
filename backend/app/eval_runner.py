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
from .evaluation.writing import benchmark_identity, generate_sample, judge_samples, load_styles
from .execution.engine import WorkflowEngine
from .execution.provider import build_provider, provider_accepts_keyless
from .model_config_service import apply_stored_model_config
from .models import WorkflowRecord
from .schemas import RunReportRequest, RunRequest
from .service import generate_run_report, get_events, get_run, get_run_metrics


RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an opt-in real-model AUV quality evaluation.")
    parser.add_argument("--scenario", default="roleplay_basic", help="Fixed scenario name")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--execute", action="store_true", help="Actually call the configured model")
    parser.add_argument("--judge", action="store_true", help="Spend one additional call on an advisory LLM review")
    parser.add_argument("--style", action="append", default=[], help="Fixed writing profile, e.g. novel or screenplay (one call each)")
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
        style_names = args.style or scenario.get("styles", [])
        load_styles(style_names)
        _validate_scenario_safety(workflow)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    estimated_calls = int(scenario.get("estimated_calls", 0)) + len(style_names) + (1 if args.judge else 0)
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
        artifact, result_path = asyncio.run(_execute(args.scenario, scenario, workflow, budget, args.judge, style_names))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] evaluation could not start: {exc}", file=sys.stderr)
        return 1
    print(f"Run status: {artifact['run']['status']}")
    if not scenario.get("writing_source"):
        print(f"Rule score: {artifact['deterministic_evaluation']['score']}/100")
    for item in (artifact.get("llm_judge") or {}).get("samples", []):
        score = f"{item['score']}/100" if item.get("score") is not None else "unavailable"
        print(f"{item['style']} quality: {score}")
    print(f"Calls: {artifact['usage']['calls']}; estimated tokens: {artifact['usage']['input_tokens'] + artifact['usage']['output_tokens']}")
    print(f"Estimated cost: ${artifact['usage']['estimated_cost_usd']:.6f}")
    print(f"Review: {result_path}")
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
    styles: list[str] | None = None,
) -> tuple[dict[str, Any], Path]:
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
    profiles = load_styles(styles or scenario.get("styles", []))
    if scenario.get("writing_source"):
        return await _execute_writing(scenario_name, scenario, limited_provider, budget, use_judge, profiles)
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
        deterministic = evaluate_run(
            detail.model_dump(mode="json"),
            scenario.get("expected", {}),
            [event.model_dump(mode="json") for event in events.events],
        )
        samples = []
        if detail.status == "succeeded":
            for profile in profiles:
                try:
                    report = await generate_run_report(
                        db, limited_provider, run_id,
                        RunReportRequest(mode="narrative", length="short", style_prompt=profile["prompt"]),
                    )
                    samples.append({"style": profile["id"], "version": profile["version"], "text": report.report_markdown})
                except Exception as exc:  # noqa: BLE001
                    samples.append({"style": profile["id"], "error": str(exc)})
                    break
        previous = _latest_result(scenario_name)
        comparison = _compare_previous(previous, deterministic, limited_provider.usage.input_tokens + limited_provider.usage.output_tokens)
        artifact = {
            "schema_version": 2,
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
            "duration_ms": metrics.duration_ms,
            "goal": workflow.environment.simulation.get("task_contract", {}).get("objective", workflow.environment.scenario),
            "deterministic_evaluation": deterministic,
            "llm_judge": None,
            "final_text": str(detail.output.get("final", {}).get("content", "")),
            "narratives": samples,
            "comparison": comparison,
        }
        if use_judge and detail.status == "succeeded":
            try:
                if profiles:
                    source = "\n".join(item["action"] for item in deterministic["actions"])
                    artifact["llm_judge"] = await judge_samples(limited_provider, source, samples, profiles)
                else:
                    artifact["llm_judge"] = await judge_run(limited_provider, scenario.get("rubric", {}), _review_markdown(artifact))
            except Exception as exc:  # noqa: BLE001
                artifact["llm_judge"] = {"error": str(exc)}
        artifact["usage"] = limited_provider.usage.model_dump(budget)
    finally:
        db.close()
        eval_engine.dispose()

    return artifact, _save_result(scenario_name, artifact)


async def _execute_writing(
    name: str, scenario: dict[str, Any], provider: BudgetedProvider, budget: EvalBudget,
    use_judge: bool, profiles: list[dict[str, Any]],
) -> tuple[dict[str, Any], Path]:
    source = scenario["writing_source"]
    samples = []
    error = None
    try:
        for profile in profiles:
            samples.append(await generate_sample(provider, source, profile))
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    complete = len(samples) == len(profiles) and bool(samples) and all(item["text"] for item in samples)
    artifact = {
        "schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario": {"id": name, "title": scenario["title"]},
        "model": {"model": provider.provider.default_model},
        "run": {"status": "succeeded" if complete else "failed", "error": error},
        "goal": "Fixed source, fixed profile requirements, independently scored forms.",
        "source_text": source,
        "benchmark": {
            **benchmark_identity(source, profiles),
            "judge_model": {
                "name": provider.provider.default_model,
                "provider": getattr(provider.provider, "provider_kind", ""),
                "endpoint": _safe_base_url(getattr(provider.provider, "base_url", "")),
                "thinking_mode": getattr(provider.provider, "thinking_mode", None),
            },
        },
        "narratives": samples, "llm_judge": None,
        "deterministic_evaluation": {"score": 100 if complete else 0, "actions": [], "findings": [] if complete else ["Writing generation incomplete."]},
    }
    if use_judge and complete:
        try:
            artifact["llm_judge"] = await judge_samples(provider, source, samples, profiles)
        except Exception as exc:  # noqa: BLE001
            artifact["llm_judge"] = {"error": str(exc)}
    artifact["usage"] = provider.usage.model_dump(budget)
    previous = _latest_result(name)
    if previous and previous.get("benchmark") == artifact["benchmark"]:
        artifact["previous_style_scores"] = previous.get("style_scores", [])
    return artifact, _save_result(name, artifact)


def _save_result(name: str, artifact: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.md"
    path.write_text(_review_markdown(artifact), encoding="utf-8")
    return path


def _latest_result(scenario_name: str) -> dict[str, Any] | None:
    path = RESULTS_DIR / f"{scenario_name}.md"
    if not path.is_file():
        return None
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        value = json.loads(first_line.removeprefix("<!-- AUV_EVAL ").removesuffix(" -->"))
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _compare_previous(previous: dict[str, Any] | None, current: dict[str, Any], current_tokens: int) -> dict[str, Any]:
    if not previous:
        return {"verdict": "first_run", "score_delta": None, "token_delta": None}
    old_score = float(previous.get("deterministic_evaluation", {}).get("score", 0))
    old_usage = previous.get("usage", {})
    old_tokens = int(old_usage.get("input_tokens", 0)) + int(old_usage.get("output_tokens", 0))
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
    judge = artifact.get("llm_judge")
    metadata = {
        "created_at": artifact["created_at"],
        "model": artifact["model"]["model"],
        "run": artifact["run"],
        "usage": usage,
        "deterministic_evaluation": {"score": evaluation["score"]},
        "benchmark": artifact.get("benchmark"),
        "style_scores": [
            {key: item.get(key) for key in ("style", "version", "score", "scores")}
            for item in judge.get("samples", [])
        ] if judge else [],
    }
    lines = [
        "<!-- AUV_EVAL " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->",
        f"# {artifact['scenario']['title']}",
        "",
        f"{artifact['run']['status']} · {artifact['model']['model']} · {usage['calls']} calls · ${usage['estimated_cost_usd']:.6f} estimated",
        "",
        str(artifact.get("goal", "")),
        "",
    ]
    if artifact.get("source_text"):
        lines.extend(["## Source", "", artifact["source_text"], ""])
    for sample in artifact.get("narratives", []):
        lines.extend([f"## {sample['style']}", "", sample.get("text") or f"Generation failed: {sample.get('error', '')}", ""])
    if evaluation.get("actions"):
        lines.extend(["## Simulation", ""])
    for item in evaluation.get("actions", []):
        lines.extend([f"**{item['node_id']}**: {item['action']}", ""])
    if artifact.get("final_text") and not artifact.get("narratives"):
        lines.extend(["## Outcome", "", artifact["final_text"], ""])
    for finding in evaluation.get("findings", []):
        lines.append(f"- {finding}")
    if artifact["run"].get("error"):
        lines.append(f"Error: {artifact['run']['error']}")
    if judge is not None:
        lines.extend(["", "## Review", ""])
        for item in judge.get("samples", []):
            previous = next((row for row in artifact.get("previous_style_scores", []) if row.get("style") == item["style"]), {})
            delta = item["score"] - previous["score"] if item.get("score") is not None and previous.get("score") is not None else None
            comparison = f" · change {delta:+}" if delta is not None else ""
            score = f"{item['score']}/100" if item.get("score") is not None else "unavailable"
            lines.append(f"{item['style']} v{item['version']}: {score}{comparison} · {json.dumps(item.get('scores', {}), ensure_ascii=False)}")
            lines.extend(f"- {issue}" for issue in item.get("issues", []))
            if item.get("error"):
                lines.append(item["error"])
        if not judge.get("samples"):
            lines.append(f"{judge.get('verdict', 'unavailable')} · {judge.get('overall_score', '-')}/100")
        lines.extend(f"- {item}" for item in judge.get("problems", [])[:3])
        if judge.get("recommendation") or judge.get("error"):
            lines.append(str(judge.get("recommendation") or judge.get("error")))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
