from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .acceptance import fingerprint
from .judging import JUDGE_SYSTEM, JUDGE_VERSION, RUBRICS

ASSETS = Path(__file__).resolve().parents[2] / "evals"


def load_cases(calibration: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads((ASSETS / ("judge_calibration.json" if calibration else "behavior_cases.json")).read_text(encoding="utf-8"))
    cases = data["cases"]
    if data.get("version") != (1 if calibration else 2) or not cases or len({row["id"] for row in cases}) != len(cases):
        raise ValueError("invalid or duplicate evaluation cases")
    for row in cases:
        if row.get("capability") not in RUBRICS or not isinstance(row.get("requirements"), str):
            raise ValueError("invalid case capability/requirements")
        if calibration:
            if row.get("label") not in {"pass", "fail", "uncertain"} or not isinstance(row.get("evidence"), dict):
                raise ValueError("invalid calibration case")
        elif not isinstance(row.get("input"), dict) or type(row.get("estimated_calls")) is not int or row["estimated_calls"] < 1:
            raise ValueError("invalid target input or preflight estimate")
        elif row["capability"] == "actor" and set(row["input"].get("role", {})) - {
                "entity_type", "entity_name", "role", "entity_profile", "identity_contract", "behavior_prompt", "responsibilities"}:
            raise ValueError("behavior cases cannot override endpoints or execute external agents/tools")
        if not calibration:
            gold = row.get("gold", {})
            if (gold.get("version") != 1 or gold.get("review_status") not in {"pending_human", "approved"}
                    or any(not isinstance(gold.get(key), list) or not gold[key] or any(not isinstance(value, str) or not value.strip() for value in gold[key])
                           for key in ("pass_conditions", "fail_conditions", "allowed_variants"))
                    or not isinstance(gold.get("contrast_example"), str) or not gold["contrast_example"].strip()
                    or gold.get("review_status") == "approved" and not str(gold.get("reviewed_by", "")).strip()):
                raise ValueError("Gold case needs explicit oracles, variation bounds, contrast and truthful review metadata")
    return data, cases


def case_fingerprint(cases: list[dict[str, Any]]) -> str:
    references = {}
    for case in cases:
        payload = case.get("input", {})
        if "scenario" in payload:
            name = payload["scenario"]
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+", name):
                raise ValueError("invalid referenced scenario ID")
            references[f"scenario:{name}"] = json.loads((ASSETS / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))
        if case["capability"] == "memory" and "checkpoint" in payload:
            references["sparse_memory"] = json.loads((ASSETS / "long_horizon" / "worldbook_distributed.json").read_text(encoding="utf-8"))
    return fingerprint({"cases": cases, "references": references})


def provider_identity(provider: Any) -> dict[str, Any]:
    from ..eval_runner import _safe_base_url
    return {"provider": getattr(provider, "provider_kind", ""), "model": getattr(provider, "default_model", ""),
            "endpoint": _safe_base_url(getattr(provider, "base_url", "")), "thinking_mode": getattr(provider, "thinking_mode", None)}


def judge_identity(provider: Any, calibration: dict[str, Any]) -> dict[str, Any]:
    return {"judge": provider_identity(provider), "contract": fingerprint({"version": JUDGE_VERSION, "system": JUDGE_SYSTEM, "rubrics": RUBRICS}),
            "calibration": fingerprint(calibration)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    counts = Counter(row["status"] for row in rows)
    repeats: dict[str, set[str]] = {}
    trials: dict[str, int] = Counter(row["case_id"] for row in rows)
    for row in rows:
        if row["status"] != "not_run":
            repeats.setdefault(row["case_id"], set()).add(row["status"])
    return {"requested_samples": count, "observed_samples": count - counts["not_run"],
            "trials": min(trials.values(), default=0), "counts": dict(counts),
            "pass_rate": counts["pass"] / count if count else 0,
            "uncertain_rate": (counts["uncertain"] + counts["not_evaluated"] + counts["not_run"]) / count if count else 1,
            "error_rate": counts["error"] / count if count else 0,
            "unstable_cases": sorted(key for key, verdicts in repeats.items() if len(verdicts) > 1),
            "failure_types": dict(Counter(row["failure_type"] for row in rows if row.get("failure_type"))),
            **{key: sum(row.get("usage", {}).get(key, 0) for row in rows)
               for key in ("calls", "input_tokens", "output_tokens", "estimated_cost_usd")}}


def calibration_report(rows: list[dict[str, Any]], dataset: dict[str, Any]) -> dict[str, Any]:
    labels = {row["id"]: row["label"] for row in dataset["cases"]}
    matrix = Counter((labels[row["case_id"]], row["status"]) for row in rows)
    negatives = sum(count for (expected, _), count in matrix.items() if expected == "fail")
    positives = sum(count for (expected, _), count in matrix.items() if expected == "pass")
    return {"label_status": dataset.get("review_status", "pending_human"),
            "agreement_rate": sum(count for (expected, observed), count in matrix.items() if expected == observed) / len(rows) if rows else None,
            "false_accept_rate": matrix["fail", "pass"] / negatives if negatives else None,
            "false_reject_rate": matrix["pass", "fail"] / positives if positives else None,
            "confusion": {f"{expected}->{observed}": count for (expected, observed), count in sorted(matrix.items())}}


def calibration_status(dataset: dict[str, Any], record: dict[str, Any] | None, identity: dict[str, Any], capability: str) -> str:
    if dataset.get("review_status") != "approved" or not str(dataset.get("reviewed_by", "")).strip():
        return "provisional"
    if not record or record.get("judge_identity") != identity:
        return "uncalibrated"
    stored = record.get("calibrations", {}).get(capability)
    if not isinstance(stored, dict):
        return "uncalibrated"
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stored["created_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return "uncalibrated"
    if not 0 <= age <= 30 * 86400:
        return "stale"
    rows = stored.get("samples", [])
    labels = {row["id"]: row["label"] for row in dataset["cases"]}
    if not {"pass", "fail"}.issubset({labels.get(row.get("case_id")) for row in rows}):
        return "uncalibrated"
    report = calibration_report(rows, dataset)
    return "calibrated" if report["agreement_rate"] >= 0.8 and report["false_accept_rate"] == 0 and all(
        row["status"] not in {"error", "not_run"} for row in rows) else "failed_calibration"
