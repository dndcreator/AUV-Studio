"""Zero-model-call cost/latency attribution over existing evaluation evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("profile measurements must be finite nonnegative numbers")
    return value


def cost_profile(record: dict[str, Any], *, input_rate: float = 1, output_rate: float = 4) -> dict[str, Any]:
    input_rate, output_rate = _number(input_rate), _number(output_rate)
    requests = record.get("requests", [])
    aggregate = record.get("usage_by_kind", record.get("diagnostics", {}).get("usage_by_kind", {}))
    groups: dict[str, dict[str, Any]] = {}
    timing = {}
    spans = []
    unknown_duration = 0
    for item in requests:
        kind = item.get("kind", "unattributed")
        row = timing.setdefault(kind, {"durations": [], "failed": 0, "reserved_calls": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0})
        row["calls"] += 1
        row["failed"] += item.get("status") == "error"
        row["reserved_calls"] += "reserved" in item.get("token_source", {}).values()
        for field in ("input_tokens", "output_tokens"):
            row[field] += _number(item.get(field, 0))
        if item.get("duration_ms") is not None:
            duration = _number(item["duration_ms"])
            row["durations"].append(duration)
            if item.get("start_offset_ms") is not None:
                start = _number(item["start_offset_ms"])
                spans.append((start, start + duration))
        else:
            unknown_duration += 1
    # Legacy traces have no module labels. Preserve their known aggregate costs,
    # but do not invent per-module latency by matching token counts or call order.
    attributable = bool(requests) and all(item.get("kind") for item in requests)
    source = timing if attributable or not aggregate else aggregate
    for kind, item in source.items():
        inputs, outputs = _number(item.get("input_tokens", 0)), _number(item.get("output_tokens", 0))
        durations = sorted(timing.get(kind, {}).get("durations", [])) if attributable else []
        cost = (inputs * input_rate + outputs * output_rate) / 1_000_000
        groups[kind] = {"calls": item.get("calls", 0), "failed": item.get("failed", 0),
                       "input_tokens": inputs, "output_tokens": outputs, "estimated_cost_usd": round(cost, 8),
                       "request_time_ms": sum(durations) if durations else None,
                       "mean_ms": sum(durations) / len(durations) if durations else None,
                       "p95_ms": durations[max(0, math.ceil(len(durations) * .95) - 1)] if durations else None,
                       "reserved_calls": timing.get(kind, {}).get("reserved_calls", 0) if attributable else None}
    total_cost = sum(row["estimated_cost_usd"] for row in groups.values())
    request_time = sum(sum(item["durations"]) for item in timing.values())
    busy = 0
    end = 0
    for start, stop in sorted(spans):
        busy += max(0, stop - max(start, end)); end = max(end, stop)
    for row in groups.values():
        row["cost_share"] = row["estimated_cost_usd"] / total_cost if total_cost else 0
        row["request_time_share"] = row["request_time_ms"] / request_time if row["request_time_ms"] is not None and request_time else None
    wall = record.get("wall_ms")
    if wall is not None: wall = _number(wall)
    return {"schema_version": 1, "source_created_at": record.get("created_at"), "rates": {"input_per_million": input_rate, "output_per_million": output_rate},
            "estimated_cost_usd": round(total_cost, 8), "modules": groups,
            "cost_ranking": sorted(groups, key=lambda key: groups[key]["estimated_cost_usd"], reverse=True),
            "latency_ranking": sorted((key for key in groups if groups[key]["request_time_ms"] is not None),
                                      key=lambda key: groups[key]["request_time_ms"], reverse=True),
            "request_time_ms": request_time if timing and not unknown_duration else None,
            "wall_ms": wall, "model_busy_wall_ms": busy if spans and len(spans) == len(requests) else None,
            "outside_request_wall_ms": max(0, wall - busy) if wall is not None and spans and len(spans) == len(requests) else None,
            "unattributed_requests": sum(1 for item in requests if not item.get("kind")),
            "attribution": "request_trace" if attributable else "legacy_aggregate" if aggregate else "unattributed_trace" if requests else "unavailable",
            "limitations": ["Costs use supplied rates, not invoices; missing usage is estimated and failed calls may be reserved.",
                            "Summed request durations are not wall time or a parallel critical path.",
                            "Outside-request time includes local work, scheduling and retry waits; prices do not account for cache discounts or model-specific rates.",
                            "Audit includes the co-generated State Coder patch; their shared call cannot be split reliably.",
                            "No behavior quality score or performance improvement is inferred from this profile."]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-rate", type=float, default=1)
    parser.add_argument("--output-rate", type=float, default=4)
    args = parser.parse_args(argv)
    if args.record.resolve() == args.output.resolve():
        parser.error("profile output must not overwrite the source evidence")
    content = args.record.read_text(encoding="utf-8-sig")
    if content.startswith("<!-- AUV_EVAL "):
        content = content.split(" -->", 1)[0][len("<!-- AUV_EVAL "):]
    record = json.loads(content)
    profile = cost_profile(record, input_rate=args.input_rate, output_rate=args.output_rate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: profile[key] for key in ("attribution", "estimated_cost_usd", "cost_ranking", "latency_ranking")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
