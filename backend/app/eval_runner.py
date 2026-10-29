from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db import Base, SessionLocal
from .evaluation.budget import BudgetedProvider, EvalBudget
from .evaluation.diagnostics import check_context_summary, round_audit_summary
from .evaluation.acceptance import acceptance, acceptance_exit, fingerprint
from .evaluation.judging import build_judge_provider, judge_evidence, JUDGE_SYSTEM, RUBRICS
from .evaluation.quality import evaluate_run
from .evaluation.scenario import list_scenarios, load_scenario
from .evaluation.writing import benchmark_identity, generate_sample, judge_samples, load_styles
from .execution.engine import WorkflowEngine
from .execution.provider import build_provider, provider_accepts_keyless
from .execution.simulation_loop import build_loop_policy
from .model_config_service import apply_stored_model_config
from .models import WorkflowRecord
from .schemas import PlanCompileRequest, RunReportRequest, RunRequest
from .plan_compiler import compile_workflow_from_plan
from .service import generate_run_report, get_events, get_run, get_run_metrics


RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an opt-in real-model AUV quality evaluation.")
    parser.add_argument("--scenario", default="roleplay_basic", help="Fixed scenario name")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--execute", action="store_true", help="Actually call the configured model")
    parser.add_argument("--judge", action="store_true", help="Spend one additional call on an advisory LLM review")
    parser.add_argument("--style", action="append", default=[], help="Fixed writing profile (two calls each; at most four with correction)")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-total-tokens", type=int, default=26_000)
    parser.add_argument("--max-output-tokens", type=int, default=600)
    parser.add_argument("--planning-output-tokens", type=int, default=2400, help="Independent planning output ceiling; same total budget")
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

    policy = build_loop_policy(workflow)
    check_calls = policy.max_actions if policy.enabled else 0
    estimated_calls = int(scenario.get("estimated_calls", 0)) + check_calls + 2 * len(style_names) + (1 if args.judge else 0)
    print(f"Scenario: {scenario.get('title', args.scenario)}")
    print(f"Expected calls: about {estimated_calls}; hard limit: {budget.max_calls}")
    print(f"Token hard limit: {budget.max_total_tokens}; output/call: {budget.max_output_tokens_per_call}")
    print(f"Planning output/call: {budget.planning_output_tokens}; core recovery: at most {min(budget.planning_output_tokens, 1600)}")
    print(f"Cost hard limit: ${budget.max_cost_usd:.2f} (using the supplied price assumptions)")
    print("Billing note: the USD limit is an estimate; call and token limits are the enforceable safeguards.")
    if check_calls:
        print(f"Includes up to {check_calls} action checks; each repair can add 2 calls within the same hard limits.")
    print("Transient requests may retry once; every physical attempt uses the same call/token/cost limits.")
    print("Invalid action-check envelopes may be rechecked once without reissuing the actor; the same hard limits apply.")
    if estimated_calls > budget.max_calls:
        label = "ERROR" if args.execute else "WARNING"
        print(f"[{label}] expected calls exceed the configured hard limit", file=sys.stderr)
        if args.execute:
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
    result_status = artifact.get("result_status", artifact["run"]["status"])
    print(f"Deliverable status: {result_status}")
    if not scenario.get("writing_source"):
        print(f"Rule score: {artifact['deterministic_evaluation']['score']}/100")
    behavior = artifact["deterministic_evaluation"].get("behavior")
    if behavior:
        print(f"Behavior validation: {behavior['status']}; long horizon: {behavior['long_horizon']['status']}")
    for item in (artifact.get("llm_judge") or {}).get("samples", []):
        score = f"{item['score']}/100" if item.get("score") is not None else "unavailable"
        print(f"{item['style']} quality: {score}")
    print(f"Calls: {artifact['usage']['calls']}; estimated tokens: {artifact['usage']['input_tokens'] + artifact['usage']['output_tokens']}")
    print(f"Estimated cost: ${artifact['usage']['estimated_cost_usd']:.6f}")
    print(f"Review: {result_path}")
    print(f"Acceptance: {acceptance(artifact)['overall']} (execution and semantic quality are separate)")
    return acceptance_exit(artifact)


def _build_budget(args: argparse.Namespace) -> EvalBudget:
    if not 1 <= args.max_calls <= 40:
        raise ValueError("max-calls must be between 1 and 40")
    if not 1_000 <= args.max_total_tokens <= 100_000:
        raise ValueError("max-total-tokens must be between 1000 and 100000")
    if not 64 <= args.max_output_tokens <= 4_000:
        raise ValueError("max-output-tokens must be between 64 and 4000")
    if not 512 <= args.planning_output_tokens <= 4_000:
        raise ValueError("planning-output-tokens must be between 512 and 4000")
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
        planning_output_tokens=args.planning_output_tokens,
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
    # The budget wrapper, not a hidden inner loop, counts every physical attempt.
    provider.retry_count = 0
    if (urlsplit(provider.base_url).hostname or "").lower().endswith("deepseek.com"):
        provider.thinking_mode = "disabled"

    limited_provider = BudgetedProvider(provider, budget, retry_count=1)
    judge_provider = limited_provider.with_provider(build_judge_provider()) if use_judge else None
    profiles = load_styles(styles or scenario.get("styles", []))
    if scenario.get("writing_source"):
        return await _execute_writing(scenario_name, scenario, limited_provider, budget, use_judge, profiles, judge_provider=judge_provider)
    generation = None
    if scenario.get("plan_text"):
        try:
            compiled = await compile_workflow_from_plan(limited_provider, PlanCompileRequest(
                plan_text=scenario["plan_text"], max_agents=int(scenario.get("max_agents", 2)), language="zh",
            ))
            workflow = compiled.workflow
            generation = compiled.generation
            limits = scenario.get("run_limits", {})
            simulation = workflow.environment.simulation
            simulation["horizon_rounds"] = min(int(simulation.get("horizon_rounds", 2) or 2), int(limits.get("rounds", 2)))
            simulation["output_contract"] = {**simulation.get("output_contract", {}), "unit": "action", "max_items": int(limits.get("actions", 3))}
            _validate_scenario_safety(workflow)
            cap = max(64, min(int(limits.get("output_tokens", budget.max_output_tokens_per_call)), budget.max_output_tokens_per_call))
            limited_provider.budget = replace(budget, max_output_tokens_per_call=cap)
            provider.max_output_tokens = cap
        except Exception as exc:  # Preserve a failed real decomposition instead of losing the evaluation record.
            artifact = {
                "schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
                "scenario": {"id": scenario_name, "title": scenario.get("title", scenario_name), "version": scenario.get("version", 1)},
                "model": {"model": provider.default_model, "provider": provider.provider_kind, "base_url": _safe_base_url(provider.base_url)},
                "budget": asdict(budget), "usage": limited_provider.usage.model_dump(budget),
                "run": {"status": "failed", "error": {"type": type(exc).__name__, "message": str(exc)[:500],
                         "code": getattr(exc, "code", "compile_failed"), "diagnostics": getattr(exc, "diagnostics", [])}},
                "goal": scenario["plan_text"], "deterministic_evaluation": {"score": 0, "actions": []},
                "llm_judge": None, "transport_retries": limited_provider.retries,
                "diagnostics": {"usage_by_kind": limited_provider.usage_by_kind},
            }
            return artifact, _save_result(scenario_name, artifact)
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
                        RunReportRequest(mode="narrative", title=scenario.get("title"), length="short", style_prompt=profile["prompt"]),
                    )
                    samples.append({"style": profile["id"], "version": profile["version"], "text": report.report_markdown,
                                    "fidelity": report.fidelity})
                except Exception as exc:  # noqa: BLE001
                    samples.append({"style": profile["id"], "error": str(exc)})
                    break
        previous = _latest_result(scenario_name)
        benchmark = {"version": 1, "case_hash": fingerprint(scenario), "profiles_hash": fingerprint(profiles),
                     "model": {"provider": provider.provider_kind, "endpoint": _safe_base_url(provider.base_url),
                               "name": provider.default_model, "thinking_mode": getattr(provider, "thinking_mode", None)},
                     "budget": asdict(budget), "judge_contract": fingerprint({"system": JUDGE_SYSTEM, "rubrics": RUBRICS}),
                     "judge_model": ({"name": judge_provider.provider.default_model,
                                       "endpoint": _safe_base_url(judge_provider.provider.base_url)} if judge_provider else None)}
        comparison = _compare_previous(previous, deterministic, limited_provider.usage.input_tokens + limited_provider.usage.output_tokens, benchmark)
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
            "result_status": "partial" if any(item.get("error") for item in samples) else detail.status,
            "duration_ms": metrics.duration_ms,
            "goal": workflow.environment.simulation.get("task_contract", {}).get("objective", workflow.environment.scenario),
            "deterministic_evaluation": deterministic,
            "llm_judge": None,
            "final_text": str(detail.output.get("final", {}).get("content", "")),
            "narratives": samples,
            "comparison": comparison,
            "benchmark": benchmark,
            "compilation": generation,
            "transport_retries": limited_provider.retries,
            "completion": detail.output.get("completion"),
            "diagnostics": {
                "usage_by_kind": limited_provider.usage_by_kind,
                "action_check_context": check_context_summary(events.events),
                "round_audits": round_audit_summary(events.events),
                "initial_phase": (workflow.environment.simulation.get("phases") or [{}])[0],
                "coordinates": {"time": workflow.environment.time_context, "location": workflow.environment.spatial_context},
                "last_audits": [{key: event.payload.get(key) for key in
                                  ("round", "decision", "task_complete", "completion_reason", "scene_control")}
                                for event in events.events if event.event == "episode_audited"][-2:],
            },
            "consistency": {
                "checked": sum(event.event == "action_checked" for event in events.events),
                "not_accepted": [
                    {"node": event.node_id, "attempt": event.payload.get("attempt"),
                     "assessment": event.payload.get("assessment", {}),
                     "diagnostic": (event.payload.get("diagnostic", "")
                                    if event.payload.get("assessment", {}).get("error_type") == "ActionConsistencyError" else ""),
                     "candidate": str(event.payload.get("candidate", {}).get("action", ""))[:600]}
                    for event in events.events if event.event == "action_checked"
                    and event.payload.get("assessment", {}).get("decision") != "accept"
                ],
            },
        }
        if use_judge and detail.status == "succeeded":
            try:
                if profiles:
                    source = "\n".join(item["action"] for item in deterministic["actions"])
                    artifact["llm_judge"] = await judge_samples(judge_provider, source, samples, profiles)
                else:
                    artifact["llm_judge"] = await judge_evidence(judge_provider, "system", {
                        "task": scenario.get("plan_text") or scenario.get("input", {}),
                        "accepted_actions": detail.output.get("actions", deterministic["actions"]),
                        "state": detail.output.get("simulation", {}), "completion": detail.output.get("completion", {}),
                        "audits": round_audit_summary(events.events),
                    }, json.dumps(scenario.get("rubric", {}), ensure_ascii=False))
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
    *, judge_provider: BudgetedProvider | None = None,
) -> tuple[dict[str, Any], Path]:
    if use_judge and judge_provider is None:
        judge_provider = provider.with_provider(build_judge_provider())
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
                "name": judge_provider.provider.default_model if judge_provider else None,
                "provider": getattr(judge_provider.provider, "provider_kind", "") if judge_provider else None,
                "endpoint": _safe_base_url(getattr(judge_provider.provider, "base_url", "")) if judge_provider else None,
                "thinking_mode": getattr(judge_provider.provider, "thinking_mode", None) if judge_provider else None,
            },
        },
        "narratives": samples, "llm_judge": None,
        "diagnostics": {"usage_by_kind": provider.usage_by_kind},
        "deterministic_evaluation": {"score": 100 if complete else 0, "actions": [], "findings": [] if complete else ["Writing generation incomplete."]},
    }
    if use_judge and complete:
        try:
            artifact["llm_judge"] = await judge_samples(judge_provider, source, samples, profiles)
        except Exception as exc:  # noqa: BLE001
            artifact["llm_judge"] = {"error": str(exc)}
    artifact["usage"] = provider.usage.model_dump(budget)
    previous = _latest_result(name)
    if previous and previous.get("benchmark") == artifact["benchmark"]:
        artifact["previous_style_scores"] = previous.get("style_scores", [])
    return artifact, _save_result(name, artifact)


def _save_result(name: str, artifact: dict[str, Any]) -> Path:
    if artifact.get("llm_judge"):
        artifact.setdefault("judge_calibration", "not_calibrated")
    artifact["acceptance"] = acceptance(artifact)
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


def _compare_previous(previous: dict[str, Any] | None, current: dict[str, Any], current_tokens: int,
                      benchmark: dict[str, Any] | None = None) -> dict[str, Any]:
    if not previous:
        return {"verdict": "first_run", "score_delta": None, "token_delta": None}
    if not benchmark or previous.get("benchmark") != benchmark:
        return {"verdict": "incomparable", "score_delta": None, "token_delta": None}
    old_score = float(previous.get("deterministic_evaluation", {}).get("score", 0))
    old_usage = previous.get("usage", {})
    old_tokens = int(old_usage.get("input_tokens", 0)) + int(old_usage.get("output_tokens", 0))
    delta = round(float(current.get("score", 0)) - old_score, 1)
    return {"verdict": "single_sample_only", "score_delta": delta, "token_delta": current_tokens - old_tokens}


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
        "result_status": artifact.get("result_status", artifact["run"]["status"]),
        "manual_review": artifact.get("manual_review"),
        "acceptance": acceptance(artifact),
        "judge_calibration": artifact.get("judge_calibration"),
        "completion": artifact.get("completion"),
        "diagnostics": artifact.get("diagnostics"),
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
        f"{artifact.get('result_status', artifact['run']['status'])} · {artifact['model']['model']} · {usage['calls']} calls · ${usage['estimated_cost_usd']:.6f} estimated",
        "",
        str(artifact.get("goal", "")),
        "",
    ]
    if artifact.get("source_text"):
        lines.extend(["## Source", "", artifact["source_text"], ""])
    for sample in artifact.get("narratives", []):
        lines.extend([f"## {sample['style']}", "", sample.get("text") or f"Generation failed: {sample.get('error', '')}", ""])
        if sample.get("fidelity"):
            checks = sample["fidelity"]["checks"]
            lines.extend([f"Fidelity checks: {', '.join(row['decision'] for row in checks)}.", ""])
    if evaluation.get("actions"):
        lines.extend(["## Simulation", ""])
    for item in evaluation.get("actions", []):
        lines.extend([f"**{item['node_id']}**: {item['action']}", ""])
    consistency = artifact.get("consistency", {})
    if consistency:
        lines.extend(["## Action Checks", "", f"{consistency['checked']} checked candidates; rejected/uncertain candidates below did not occur.", ""])
        context = artifact.get("diagnostics", {}).get("action_check_context", {})
        if context.get("measured_checks"):
            lines.extend([f"Input estimate: {context['estimated_input_tokens']} tokens; unprojected estimate: "
                          f"{context['unprojected_input_tokens']}; exact aliases: {context['aliases']}.", ""])
    audits = artifact.get("diagnostics", {}).get("round_audits", [])
    if audits:
        lines.extend(["Goal audits: " + "; ".join(
            f"round {row['round']} {row['decision']}, no-goal-progress streak={row['goal_stagnation']}, "
            f"unassessed={row['unassessed_goal_rounds']}" for row in audits), ""])
    if consistency:
        for row in consistency["not_accepted"]:
            assessment = row["assessment"]
            lines.extend([f"- {row['node']} attempt {row['attempt']}: {assessment.get('decision')} "
                          f"({assessment.get('reason') or assessment.get('error_type', '')})",
                          f"  Candidate, not an accepted event: {row['candidate']}"])
            if row.get("diagnostic"):
                lines.append(f"  Protocol diagnostic: {row['diagnostic']}")
    behavior = evaluation.get("behavior")
    if behavior:
        lines.extend(["## Behavioral Evidence", "", f"Boundary: {behavior['information_boundary']['status']}",
                      *[f"- {finding}" for finding in behavior['information_boundary']['findings']],
                      *[f"- Propagation {row['marker']}: {row['status']}; missing={row['missing']}; premature={row['premature']}" for row in behavior['propagation']],
                      f"Long horizon: {behavior['long_horizon']['status']} ({behavior['long_horizon']['observed_rounds']} rounds)",
                      f"Misunderstanding review: {behavior['misunderstanding']['question']}",
                      "Autonomy: manual review required; compare actions above with guidance below.",
                      *[f"- Director: {text or '(no guidance)'}" for text in behavior['autonomy']['director_guidance']],
                      *[f"- Router: {text}" for text in behavior['coordination']['routing']],
                      *[f"- Scene goal: {text}" for text in behavior['coordination']['scene_goals']],
                      f"- Role owners: {behavior['coordination']['roster']}",
                      f"State snapshot: {behavior['state_snapshot']} (an incomplete run is not proof of forgetting).",
                      "State (requires semantic review against actual actions):",
                      *[f"- {row['id']}: {str(row['value'])[:160]} ({row['visibility']}; sources={row['sources']})" for row in behavior['state_evidence']], ""])
    action_text = "\n".join(item["action"] for item in evaluation.get("actions", []))
    if artifact.get("compilation"):
        lines.extend([f"Plan compilation: {artifact['compilation']}", ""])
    if artifact.get("transport_retries"):
        lines.extend([f"Transport retries (included in usage): {artifact['transport_retries']}", ""])
    if artifact.get("final_text") and not artifact.get("narratives") and str(artifact["final_text"]).strip() != action_text.strip():
        lines.extend(["## Outcome", "", artifact["final_text"], ""])
    for finding in evaluation.get("findings", []):
        lines.append(f"- {finding}")
    if artifact["run"].get("error"):
        lines.append(f"Error: {artifact['run']['error']}")
    if artifact.get("manual_review"):
        review = artifact["manual_review"]
        lines.extend(["", "## Manual Review", "", f"Status: {review['status']}",
                      *[f"- {issue}" for issue in review.get("issues", [])]])
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
