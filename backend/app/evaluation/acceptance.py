from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def acceptance(artifact: dict[str, Any]) -> dict[str, str]:
    execution = "pass" if artifact.get("run", {}).get("status") == "succeeded" else "fail"
    checks = artifact.get("deterministic_evaluation", {}).get("checks", {})
    rule_score = artifact.get("deterministic_evaluation", {}).get("score")
    behavior = artifact.get("deterministic_evaluation", {}).get("behavior", {}).get("status")
    deterministic = "fail" if any(value is not True for value in checks.values()) or behavior == "fail" else "pass"
    if not checks and (type(rule_score) not in (int, float) or rule_score < 100):
        deterministic = "fail"
    quality = "not_evaluated"
    judge = artifact.get("llm_judge") or {}
    if judge:
        quality = "uncertain"
        if judge.get("verdict") in {"pass", "fail", "uncertain"} and not judge.get("error"):
            quality = judge["verdict"]
        elif isinstance(judge.get("samples"), list) and judge["samples"]:
            scores = [sample.get("score") for sample in judge["samples"]]
            if all(type(value) in (int, float) and 0 <= value <= 100 for value in scores):
                quality = "pass" if min(scores) >= 75 else "fail"
    manual = (artifact.get("manual_review") or {}).get("status")
    if manual in {"pass", "fail"}:
        quality = manual
    deliverable_failed = artifact.get("result_status", "succeeded") != "succeeded" or any(
        item.get("error") for item in artifact.get("narratives", []))
    calibrated = artifact.get("judge_calibration") == "calibrated" or manual == "pass"
    overall = ("fail" if execution == "fail" or deterministic == "fail" or quality == "fail" or deliverable_failed
               else "review_required" if quality != "pass" or behavior == "inconclusive" or not calibrated else "pass")
    return {"execution": execution, "deterministic": deterministic, "quality": quality, "overall": overall}


def acceptance_exit(artifact: dict[str, Any]) -> int:
    return {"pass": 0, "fail": 1, "review_required": 3}[acceptance(artifact)["overall"]]


def compare_baseline(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return {"status": "first_run"}
    if not current.get("benchmark") or current.get("benchmark") != previous.get("benchmark"):
        return {"status": "incomparable", "reason": "case/model/rubric/protocol/budget identity differs"}
    old = previous.get("summary", {})
    new = current.get("summary", {})
    if min(old.get("trials", 0), new.get("trials", 0)) < 2:
        return {"status": "insufficient_repeats"}
    if any(summary.get("requested_samples", 0) <= 0 or summary.get("observed_samples", 0) != summary.get("requested_samples", 0)
           for summary in (old, new)):
        return {"status": "incomplete_coverage"}
    deltas = {
        key: round(new.get(key, 0) - old.get(key, 0), 6)
        for key in ("pass_rate", "uncertain_rate", "error_rate", "input_tokens", "output_tokens", "estimated_cost_usd")}
    if current.get("mode") == "calibration":
        for key in ("agreement_rate", "false_accept_rate", "false_reject_rate"):
            before, after = previous.get("calibration_report", {}).get(key), current.get("calibration_report", {}).get(key)
            deltas[key] = round(after - before, 6) if before is not None and after is not None else None
    case_deltas = {}
    for case_id, after in new.get("by_case", {}).items():
        before = old.get("by_case", {}).get(case_id)
        if not before:
            continue
        delta = round(after["pass_rate"] - before["pass_rate"], 6)
        error_delta = round(after["error_rate"] - before["error_rate"], 6)
        case_deltas[case_id] = {"observed_direction": "mixed" if delta > 0 and error_delta > 0 else "better" if delta > 0 and error_delta <= 0
                               else "worse" if delta < 0 or error_delta > 0 else "unchanged",
                               "pass_rate_delta": delta, "error_rate_delta": error_delta,
                               "token_delta": after["input_tokens"] + after["output_tokens"] - before["input_tokens"] - before["output_tokens"]}
    return {"status": "comparable", "deltas": deltas, "by_case": case_deltas,
        "note": "Descriptive repeated-sample comparison, not statistical proof of improvement."}
