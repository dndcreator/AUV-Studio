from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evaluation.acceptance import compare_baseline, fingerprint
from .evaluation.benchmarks import ASSETS, calibration_report, calibration_status, case_fingerprint, judge_identity, load_cases, provider_identity, summarize
from .evaluation.budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from .evaluation.judging import JudgeProtocolError, RUBRICS, build_judge_provider, judge_evidence
from .evaluation.targets import run_target
from .evaluation.judge_evidence import target_evidence

RESULTS = ASSETS / "results"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Opt-in component/system behavior benchmarks; default is zero-call preflight.")
    p.add_argument("--case", action="append", default=[])
    p.add_argument("--capability", choices=sorted(RUBRICS))
    p.add_argument("--list", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--judge", action="store_true")
    p.add_argument("--calibrate", action="store_true", help="Judge fixed proposed gold samples, without running a target agent")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--max-calls", type=int, default=12)
    p.add_argument("--max-total-tokens", type=int, default=26000)
    p.add_argument("--max-output-tokens", type=int, default=900)
    p.add_argument("--max-cost-usd", type=float, default=0.10)
    p.add_argument("--input-usd-per-million", type=float, default=1.0)
    p.add_argument("--output-usd-per-million", type=float, default=4.0)
    p.add_argument("--timeout-seconds", type=int, default=120)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--promote-baseline", help="Explicit versioned baseline name; never updated by normal execution")
    return p


def _budget(args: argparse.Namespace) -> EvalBudget:
    for value, low, high, label in ((args.repeat, 1, 5, "repeat"), (args.max_calls, 1, 400, "max-calls"),
                                  (args.max_total_tokens, 1000, 1000000, "max-total-tokens"),
                                  (args.max_output_tokens, 64, 4000, "max-output-tokens"),
                                  (args.timeout_seconds, 1, 1800, "timeout-seconds")):
        if not low <= value <= high:
            raise ValueError(f"{label} must be between {low} and {high}")
    if not 0.01 <= args.max_cost_usd <= 5:
        raise ValueError("max-cost-usd must be between0.01 and5")
    if args.promote_baseline and (not re.fullmatch(r"[a-z0-9_-]+", args.promote_baseline) or args.repeat < 2):
        raise ValueError("baseline promotion needs a safe name and at least two repetitions")
    return EvalBudget(args.max_calls, args.max_total_tokens, args.max_output_tokens, args.max_cost_usd,
                      args.input_usd_per_million, args.output_usd_per_million)


def _read_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("benchmark record must be an object")
    return value


async def execute_cases(
    cases: list[dict[str, Any]], actor: BudgetedProvider, judge: BudgetedProvider | None,
    *, repeat: int, timeout_seconds: int, calibration: bool = False,
) -> list[dict[str, Any]]:
    rows = [{"case_id": case["id"], "capability": case["capability"], "trial": trial, "status": "not_run"}
            for trial in range(1, repeat + 1) for case in cases]
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    for index, row in enumerate(rows):
        case = next(item for item in cases if item["id"] == row["case_id"])
        before = actor.usage.model_dump(actor.budget)

        async def step():
            row["stage"] = "judge" if calibration else "target"
            if calibration:
                evidence = case["evidence"]
            else:
                output = await run_target(case, actor)
                row["output"] = output
                evidence = target_evidence(case, output)
                if case["capability"] == "system" and output.get("report_error"):
                    row.update(status="error", stage="deliverable", failure_type="deliverable_error", error=output["report_error"])
                    return
                if case["capability"] == "system":
                    if output.get("status") == "failed":
                        row.update(status="error", stage="simulation", failure_type="simulation_error", error=output.get("error"))
                        return
                    if output.get("status") != "succeeded":
                        row.update(status="fail", failure_type="task_incomplete")
                    if output.get("observed_rounds", 0) < case.get("min_observed_rounds", 0):
                        row.update(status="uncertain", failure_type="insufficient_long_horizon_coverage")
            if judge is not None:
                row["stage"] = "judge"
                if len(json.dumps(evidence, ensure_ascii=False)) > 64000:
                    raise ValueError("judge evidence exceeds64k characters; no silent truncation")
                gold = case.get("gold", {})
                requirements = case["requirements"] + ("\nCASE ORACLE: " + json.dumps({key: gold[key] for key in
                    ("pass_conditions", "fail_conditions", "allowed_variants") if key in gold}, ensure_ascii=False) if gold else "")
                row["judgment"] = await judge_evidence(judge, case["capability"], evidence, requirements)
                if row["status"] == "not_run":
                    row["status"] = row["judgment"]["verdict"]
                    if row["status"] == "fail":
                        row["failure_type"] = "semantic_failure"
            elif row["status"] == "not_run":
                row["status"] = "not_evaluated"

        try:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("evaluation deadline exceeded")
            await asyncio.wait_for(step(), timeout=remaining)
        except (Exception, asyncio.CancelledError) as exc:
            row.update(status="error", failure_type="budget" if isinstance(exc, EvalBudgetExceeded) else "timeout" if isinstance(exc, TimeoutError)
                       else "judge_error" if "output" in row or calibration else "target_error",
                       error={"type": type(exc).__name__, "code": str(getattr(exc, "code", "evaluation_error"))[:80]})
            if isinstance(exc, (JudgeProtocolError, EvalBudgetExceeded)):
                row["error"]["diagnostic"] = str(exc)[:200]
            if isinstance(getattr(exc, "diagnostics", None), list):
                row["error"]["diagnostics"] = exc.diagnostics
            if isinstance(getattr(exc, "issues", None), list):
                row["error"]["issues"] = exc.issues
            for pending in rows[index + 1:]:
                pending["failure_type"] = "not_run_after_error"
        after = actor.usage.model_dump(actor.budget)
        row["usage"] = {key: round(after[key] - before[key], 8) for key in after}
        row["request_range"] = {"start": before["calls"] + 1, "end": after["calls"]} if after["calls"] > before["calls"] else None
        if row["status"] == "error":
            break
    return rows


async def _execute(args: argparse.Namespace, cases: list[dict[str, Any]], budget: EvalBudget) -> dict[str, Any]:
    judge_raw = build_judge_provider() if args.judge or args.calibrate else None
    if args.calibrate:
        actor_raw = judge_raw
    else:
        from .db import SessionLocal
        from .execution.provider import build_provider, provider_accepts_keyless
        from .model_config_service import apply_stored_model_config
        actor_raw = build_provider()
        with SessionLocal() as db:
            apply_stored_model_config(db, actor_raw)
        if not actor_raw.api_key and not provider_accepts_keyless(actor_raw.provider_kind, actor_raw.base_url):
            raise ValueError("Configure the target model before executing behavior cases")
    actor = BudgetedProvider(actor_raw, budget, capture_trace=True)
    judge = actor.with_provider(judge_raw) if judge_raw is not None else None
    gold, _ = load_cases(True)
    identity = judge_identity(judge_raw, gold) if judge_raw is not None else None
    previous_calibration = _read_record(RESULTS / "calibration_latest.json")
    calibration_states = {cap: calibration_status(gold, previous_calibration, identity, cap) if identity else "not_requested"
                          for cap in {row["capability"] for row in cases}}
    if not args.calibrate:
        for cap in calibration_states:
            if calibration_states[cap] == "calibrated" and any(
                    case.get("gold", {}).get("review_status") != "approved" for case in cases if case["capability"] == cap):
                calibration_states[cap] = "provisional_gold"
    rows = await execute_cases(cases, actor, judge, repeat=args.repeat, timeout_seconds=args.timeout_seconds, calibration=args.calibrate)
    summary = summarize(rows)
    summary["by_capability"] = {cap: summarize([row for row in rows if row["capability"] == cap]) for cap in sorted(calibration_states)}
    summary["by_case"] = {case["id"]: summarize([row for row in rows if row["case_id"] == case["id"]]) for case in cases}
    sources = ("plan_compiler.py", "plan_generation.py", "plan_prompt.py", "execution/engine.py", "execution/nodes.py",
               "execution/action_consistency.py", "execution/action_evidence.py", "execution/scene_control.py",
               "execution/simulation_loop.py", "execution/dynamic_state.py", "execution/state_coding.py", "execution/memory.py",
               "execution/context_book.py", "execution/perception.py", "narrative.py", "narrative_fidelity.py", "evaluation/targets.py")
    implementation = fingerprint({path: fingerprint((Path(__file__).parent / path).read_text(encoding="utf-8")) for path in sources})
    artifact = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "mode": "calibration" if args.calibrate else "behavior",
        "benchmark": {"case_hash": case_fingerprint(cases), "actor": provider_identity(actor_raw), "judge": identity,
                      "budget": asdict(budget), "repeat": args.repeat, "timeout_seconds": args.timeout_seconds},
        "implementation": implementation, "summary": summary, "samples": rows, "usage": actor.usage.model_dump(budget),
        "calibration_status": calibration_states, "judge_identity": identity,
        "usage_by_kind": actor.usage_by_kind,
        "requests": actor.request_trace,
    }
    if args.calibrate:
        artifact["calibration_report"] = calibration_report(rows, gold)
        artifact["calibration_by_capability"] = {cap: calibration_report([row for row in rows if row["capability"] == cap], gold)
                                                 for cap in calibration_states}
        retained = previous_calibration.get("calibrations", {}) if previous_calibration and previous_calibration.get("judge_identity") == identity else {}
        artifact["calibrations"] = {**retained, **{cap: {
            "created_at": artifact["created_at"], "report": artifact["calibration_by_capability"][cap],
            "samples": [{"case_id": row["case_id"], "status": row["status"]} for row in rows if row["capability"] == cap],
        } for cap in calibration_states}}
        artifact["calibration_status"] = {cap: calibration_status(gold, artifact, identity, cap) for cap in calibration_states}
    artifact["comparison"] = compare_baseline(_read_record(args.baseline) if args.baseline else None, artifact)
    return artifact


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _, all_cases = load_cases(args.calibrate)
        if args.list:
            print("\n".join(f"{row['id']}: {row['capability']}" for row in all_cases))
            return 0
        if set(args.case) - {row["id"] for row in all_cases}:
            raise ValueError("unknown case ID")
        cases = [row for row in all_cases if (not args.case or row["id"] in args.case) and
                 (not args.capability or row["capability"] == args.capability)]
        budget = _budget(args)
        case_fingerprint(cases)  # Broken fixture references must fail before any model request.
        if not cases or (args.execute and not (args.case or args.capability or args.calibrate)):
            raise ValueError("select an explicit case/capability; full paid suites are never implicit")
        expected = args.repeat * sum(1 if args.calibrate else row["estimated_calls"] + int(args.judge) for row in cases)
        print(f"Selected {len(cases)} cases x{args.repeat}; estimated requests: {expected}; hard ceiling: {budget.max_calls}.")
        print(f"Token ceiling: {budget.max_total_tokens}; estimated USD ceiling: {budget.max_cost_usd}; deadline: {args.timeout_seconds}s.")
        print("Supplied prices apply to BOTH target and judge; use rates covering both. Estimates are not invoices.")
        if not args.execute:
            print("Preflight only; no model/configuration access. Add --execute only after authorizing this budget.")
            return 0
        if expected > budget.max_calls:
            raise ValueError("estimated requests exceed budget; reduce selection/repeats or explicitly revise the ceiling")
        if args.baseline:
            baseline = _read_record(args.baseline)
            if not baseline or not isinstance(baseline.get("benchmark"), dict) or not isinstance(baseline.get("summary"), dict):
                raise ValueError("requested baseline is missing or malformed")
        if args.promote_baseline and (ASSETS / "baselines" / f"{args.promote_baseline}.json").exists():
            raise ValueError("baseline already exists; use a new version, never overwrite")
        artifact = asyncio.run(_execute(args, cases, budget))
        RESULTS.mkdir(parents=True, exist_ok=True)
        path = RESULTS / ("calibration_latest.json" if args.calibrate else "behavior_latest.json")
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.promote_baseline:
            if any(row["status"] in {"error", "not_run", "not_evaluated"} for row in artifact["samples"]):
                raise ValueError("cannot promote an incomplete/error baseline")
            baseline_dir = ASSETS / "baselines"
            baseline_dir.mkdir(exist_ok=True)
            target = baseline_dir / f"{args.promote_baseline}.json"
            with target.open("x", encoding="utf-8") as handle:
                json.dump({key: artifact[key] for key in ("schema_version", "created_at", "mode", "benchmark", "implementation", "summary",
                                                        "calibration_status", "calibration_report", "calibration_by_capability", "calibrations") if key in artifact},
                          handle, ensure_ascii=False, indent=2)
        print(f"Results: {path}; outcomes: {artifact['summary']['counts']}; usage: {artifact['usage']}")
        if args.calibrate:
            report = artifact["calibration_report"]
            if any(row["status"] in {"error", "not_run"} for row in artifact["samples"]) or report["agreement_rate"] != 1:
                return 1
            return 0 if report["label_status"] == "approved" and all(
                value == "calibrated" for value in artifact["calibration_status"].values()) else 3
        if any(row["status"] in {"fail", "error"} for row in artifact["samples"]):
            return 1
        calibrated = all(value == "calibrated" for value in artifact["calibration_status"].values())
        return 0 if calibrated and all(row["status"] == "pass" for row in artifact["samples"]) else 3
    except Exception as exc:
        print(f"Evaluation stopped: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
